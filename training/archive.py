import sys
import warnings
import subprocess
import random
import torch
import numpy as np
import json
import datetime
from pathlib import Path
import pandas as pd

from helpers import cp

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


# Key directories: `models_location` is where all the archives are stored
git_root = Path(subprocess.check_output('git rev-parse --show-toplevel'
                                        .split()).strip().decode("utf-8"))
models_location = git_root / 'models/'


class ModelArchive(object):
    """
    Abstraction for the maintainence of trained model archives

    A `ModelArchive` is an abstaction for an underlying filesystem structure
    that saves all the relevant information about a trained model.

    It records and archives
        - the model weights
        - the model architecture
        - the state of the optimizer
        - the model's training history
        - the training parameters used
        - the state of the pseudorandom generators used
        - the loss curves
        - debugging outputs

    The archive is intended to be explicit enough that
        (a) the training results can be reproduced if desired
        (b) if training is stopped or interrupted at any point, the
            state can be read back from disk and training can continue
            almost as if it was never interrupted

    Usage:
        Create a new model archive and save the current training state:
        >>> mymodel = ModelArchive('mymodel_v01', readonly=False)
        (some training code ...)
        >>> mymodel.log(loss)
        >>> mymodel.save()  # save the updated state to disk

        Load an existing trained model and run it on data:
        >>> existing_archive = ModelArchive('existing_archive', readonly=True)
        >>> net = existing_archive.model
        >>> output = net(data)

        Create a new model archive from an existing one:
        >>> old_model = ModelArchive('old_model')
        >>> new_model = old_model.start_new('new_model_v01')
        (some training code ...)
        >>> new_model.save()  # save the updated state of the new model to disk
    """

    @classmethod
    def model_exists(cls, name):
        """
        Returns whether a trained model of this name exists
        """
        cls._check_name(name)
        return (models_location / name).is_dir()

    @classmethod
    def _check_name(cls, name):
        """
        Checks a proposed name for formatting irregularities.
        Checking this prevents accidentally writing to arbitrary
        file locations.
        """
        if not len(name):
            raise ValueError('Model name must have non-zero length.')
        if not name.replace('_', '').isalnum():
            raise ValueError('Malformated name: {}\n'
                             'Model name can only contain alphanumeric '
                             'characters and underscores _.'.format(name))

    def __init__(self, name, readonly=True, *args, **kwargs):
        self._check_name(name)  # check name formatting
        self._name = name
        self.readonly = readonly
        self.directory = models_location / self._name
        self.intermediate_models = self.directory / 'intermediate_models/'
        self.debug_outputs = self.directory / 'debug_outputs/'
        self.paths = {
            # the model's trained weights
            'weights': self.directory / 'weights.pt',
            # the state of the optimizer
            'optimizer': self.directory / 'optimizer.pt',
            # the state of the pseudorandom number gerenrators
            'prand': self.directory / 'prand.pt',
            # other paths
            'loss': self.directory / 'loss.csv',
            'command': self.directory / 'command.txt',
            'plan': self.directory / 'plan.txt',
            'history': self.directory / 'history.txt',
            'progress': self.directory / 'progress.log',
            'seed': self.directory / 'seed.txt',
            'architecture': self.directory / 'architecture.py',
            'objective': self.directory / 'objective.py',
            'preprocessor': self.directory / 'preprocessor.py',
            'commit': self.directory / 'commit.diff',
            'state_vars': self.directory / 'state_vars.json',
            'plot': self.directory / 'plot.png',
        }
        self._architecture = None
        self._model = None
        self._optimizer = None
        self._state_vars = None
        self._objective = None
        self._loss = None
        self._val_loss = None
        self._preprocessor = None
        self._current_debug_directory = None

        if ModelArchive.model_exists(name):
            self._load(*args, **kwargs)
        elif not self.readonly:
            self._create(*args, **kwargs)
        else:
            raise ValueError('Could not find a trained model named "{}".\n'
                             'If the intention was to create one, use '
                             '`ModelArchive("{}", readonly=False)`.'
                             .format(name, name))

        if not self.readonly:
            self.out = FileLog(sys.stdout, self.paths['progress'])
            self.err = FileLog(sys.stderr, self.paths['progress'])
        else:
            self.out = sys.stdout
            self.err = sys.stderr

    def _load(self, *args, **kwargs):
        if not self.readonly:
            print('Writing to exisiting model archive: {}'.format(self._name))
        else:
            print('Reading from exisiting model archive: {}'.format(self._name))
        assert self.directory.is_dir() and self.paths['commit'].exists()

        # check for matching commits
        # this can prevent errors arising from working on the wrong git branch
        saved_commit = self.commit
        current_commit = subprocess.check_output('git rev-parse HEAD'
                                                 .split()).strip()
        if int(saved_commit, 16) != int(current_commit, 16):
            print('Warning: The repository has changed since this '
                  'net was last trained.')
            if not self.readonly:
                print('Continuing may overwrite the archive by '
                      'running the new code. If this was the intent, '
                      'then it might not be a problem.'
                      '\nIf not, exit the process and return to the '
                      'old commit by running `git checkout {}`'
                      '\nDo you wish to proceed?  [y/N]'
                      .format(saved_commit))
                if input().lower() not in {'yes', 'y'}:
                    print('Exiting')
                    sys.exit()
                print('OK, proceeding...')

        # load the model, optimizer, and state variables
        self._load_state_vars(*args, **kwargs)
        kwargs.update(self._state_vars)
        self._load_model(*args, **kwargs)
        self._load_objective(*args, **kwargs)
        self._load_preprocessor(*args, **kwargs)
        self._load_optimizer(*args, **kwargs)
        # load the pseudorandom number generator last
        self._load_prand(*args, **kwargs)

    def _create(self, *args, **kwargs):
        print('Creating a new model archive: {}'.format(self._name))

        # create directories
        self.directory.mkdir()
        self.intermediate_models.mkdir()
        self.debug_outputs.mkdir()

        # create archive files
        for filename in [
            'loss.csv',
            'command.txt',
            'plan.txt',
            'history.txt',
            'progress.log',
            'seed.txt',
            'commit.diff',
        ]:
            key = filename.split('.')[0]
            self.paths[key].touch(exist_ok=False)

        # copy the architecture and objective definitions into the archive
        cp(git_root/'training'/'architecture.py', self.paths['architecture'])
        cp(git_root/'training'/'objective.py', self.paths['objective'])
        cp(git_root/'training'/'preprocessor.py', self.paths['preprocessor'])

        # record the status of the git repository
        with self.paths['commit'].open(mode='wb') as f:
            hash = subprocess.check_output('git rev-parse HEAD'.split())
            f.write(hash)
            branch = subprocess.check_output('git rev-parse --abbrev-ref HEAD'
                                             .split())
            f.write(branch)
            diff = subprocess.check_output('git diff HEAD'.split())
            f.write(diff)

        # write out the command used
        with self.paths['command'].open(mode='w') as f:
            f.writelines(' '.join(sys.argv))

        # create a history entry
        with self.paths['history'].open(mode='w') as f:
            f.writelines('Model: {}\n'.format(self._name))
            f.writelines('Time: {}\n'.format(datetime.datetime.now()))
            f.writelines('Commit: {}\n'.format(self.commit))
            f.writelines(' '.join(sys.argv) + '\n')
            f.writelines('\n')

        # when creating an archive, init pseudorandom number generator first
        self._load_prand(*args, **kwargs)
        # initialize the model, optimizer, and state variables
        self._load_state_vars(*args, **kwargs)
        self._load_model(*args, **kwargs)
        self._load_objective(*args, **kwargs)
        self._load_preprocessor(*args, **kwargs)
        self._load_optimizer(*args, **kwargs)

        self.save()

    def start_new(self, name, *args, **kwargs):
        """
        Creates and returns a new model archive initialized with the
        weights of this model.

        The new model's training history is copied from the old model
        and appended to.
        """
        if self.model_exists(name):
            raise ValueError('The model "{}" already exists.'.format(name))
        new_archive = type(self)(name, readonly=False,
                                 weights_file=self.paths['weights'],
                                 *args, **kwargs)
        cp(self.paths['weights'], new_archive.paths['weights'])
        cp(self.paths['loss'], new_archive.paths['loss'])
        cp(self.paths['progress'], new_archive.paths['progress'])

        # Copy the old history into the new archive
        tempfile = new_archive.directory / 'history.txt.temp'
        cp(new_archive.paths['history'], tempfile)
        cp(self.paths['history'], new_archive.paths['history'])
        with new_archive.paths['history'].open(mode='a') as f:
            f.writelines(tempfile.read_text())
        tempfile.unlink()  # delete the temporary file

        return new_archive

    @property
    def name(self):
        """
        The name of the model
        """
        return self._name

    @property
    def commit(self):
        """
        The git hash for the commit on which the model was first trained
        """
        saved_commit = ''
        if not self.paths['commit'].exists():
            return None
        with self.paths['commit'].open(mode='r') as f:
            saved_commit = f.readline()
        return saved_commit.strip()

    @property
    def architecture(self):
        """
        The python module code used to build the model.
        Useful for calling any non-class functions defined there.
        """
        return self._architecture

    @property
    def model(self):
        """
        A live version of the model.
        """
        return self._model

    @property
    def optimizer(self):
        """
        The model's optimizer
        """
        return self._optimizer

    @property
    def loss(self):
        """
        The loss function of the model, optimized during training
        """
        return self._loss

    @property
    def val_loss(self):
        """
        The validation loss function of the model
        """
        return self._val_loss
        return self._loss

    @property
    def preprocessor(self):
        """
        The archive's image preprocessor
        """
        return self._preprocessor

    @property
    def state_vars(self):
        """
        A dict of various training state variables

        This dict should be used to store any additional training information
        that is needed to restore the model's training state when resuming
        training.

        For convenience, elements of this dict can be accessed using
        either notation:
            >>> state_vars['item']
            or
            >>> state_vars.item
        """
        return self._state_vars

    def _load_model(self, *args, **kwargs):
        """
        Loads a working version of the model's architecture,
        initialized with its pretrained weights.

        If the model is untrained, loads a newly initialized model.
        """
        sys.path.insert(0, str(self.directory))
        import architecture
        sys.path.remove(str(self.directory))
        self._architecture = architecture
        self._model = architecture.Model(*args, **kwargs)
        if self.paths['weights'].is_file():
            self._model.load(self.paths['weights'])

        # set model to eval or train mode
        if self.readonly:
            for p in self._model.parameters():
                p.requires_grad = False
            self._model.eval().cuda()
        else:
            for p in self._model.parameters():
                p.requires_grad = True
            self._model.train().cuda()
            self._model = torch.nn.DataParallel(self._model)

        return self._model

    def _load_objective(self, *args, **kwargs):
        """
        Loads the objective functions stored in the archive
        """
        try:
            sys.path.insert(0, str(self.directory))
            import objective
        except ImportError:
            return None
        finally:
            sys.path.remove(str(self.directory))
        self._objective = objective
        self._loss = self._objective.Objective(*args, **kwargs)
        self._val_loss = self._objective.ValidationObjective(*args, **kwargs)
        if not self.readonly:
            self._loss = torch.nn.DataParallel(self._loss.cuda())
            self._val_loss = torch.nn.DataParallel(self._val_loss.cuda())
        return self._objective

    def _load_preprocessor(self, *args, **kwargs):
        """
        Loads the archive's image preprocessor
        """
        try:
            sys.path.insert(0, str(self.directory))
            import preprocessor
        except ImportError:
            return None
        finally:
            sys.path.remove(str(self.directory))
        self._preprocessor = preprocessor.Preprocessor(*args, **kwargs)
        return self._preprocessor

    def _load_optimizer(self, *args, **kwargs):
        """
        Loads the saved state of the optimizer.

        If the model is untrained, loads a newly initialized optimizer.
        """
        assert self.model is not None, 'The model has not yet been loaded.'
        self._optimizer = torch.optim.Adam(self.model.parameters())
        if self.paths['optimizer'].is_file():
            with self.paths['optimizer'].open('rb') as f:
                opt_state_dict = torch.load(f)
            self._optimizer.load_state_dict(opt_state_dict)
        return self._optimizer

    def _load_prand(self, seed=None, *args, **kwargs):
        """
        Loads the saved state of the pseudorandom number generators.
        """
        if self.readonly:
            return  # do not seed for a readonly archive
        if self.paths['prand'].is_file():
            with self.paths['prand'].open('rb') as f:
                prand_state = torch.load(f)
            set_random_generator_state(prand_state)
        else:
            with self.paths['seed'].open('w') as f:
                f.write(str(seed))
            print('Initializing seed to {}'.format(seed))
            set_seed(seed)

    def _load_state_vars(self, *args, **kwargs):
        """
        Loads the dict of state variables stored in `state_vars.json`
        """
        self._state_vars = dotdict({'name': self._name})  # default state_vars
        if self.paths['state_vars'].exists():
            with self.paths['state_vars'].open(mode='r') as f:
                self._state_vars = dotdict(json.load(f))
        return self._state_vars

    def save(self):
        """
        Saves the live model archive to disk.
        More specifically, this updates the saved archive to match the live
        training state of the model
        """
        if self.readonly:
            raise ReadOnlyError(self._name)
        if self._model:
            self._model.module.save(self.paths['weights'])
        if self._optimizer:
            with self.paths['optimizer'].open('wb') as f:
                torch.save(self._optimizer.state_dict(), f)
        with self.paths['prand'].open('wb') as f:
            torch.save(get_random_generator_state(), f)
        if self._state_vars:
            with self.paths['state_vars'].open('w') as f:
                state_vars_serializable = {
                    key: (str(value) if isinstance(value, Path) else value)
                    for (key, value) in self._state_vars.items()
                }
                f.write(json.dumps(state_vars_serializable,
                                   indent=2, sort_keys=True))

    def create_checkpoint(self, epoch, iteration, save=True):
        """
        Save a checkpoint in the training.
        This saves an snapshot of the model's current saved weights.

        Note: To just update the saved weights without creating a checkpoint,
        use `save()`.
        """
        if self.readonly:
            raise ReadOnlyError(self._name)
        if save:
            self.save()  # ensure the saved weights are up to date
        if epoch is None:
            checkpt_name = 'init.pt'
        elif iteration is None:
            checkpt_name = 'e{}.pt'.format(epoch)
        else:
            checkpt_name = 'e{}_t{}.pt'.format(epoch, iteration)
        cp(self.paths['weights'], self.intermediate_models / checkpt_name)

    def new_debug_directory(self):
        """
        Creates a new subdirectory for debugging outputs.

        The new subdirectory will be placed in the `debug_outputs` directory
        of the archive.
        """
        if self.readonly:
            raise ReadOnlyError(self._name)
        if self._state_vars.iteration is not None:
            dirname = 'e{}_t{}/'.format(self._state_vars.epoch,
                                        self._state_vars.iteration)
        else:
            dirname = 'e{}_val/'.format(self._state_vars.epoch)
        debug_directory = self.debug_outputs / dirname
        if debug_directory.is_dir():
            raise FileExistsError('The debug directory {} already exists.'
                                  .format(debug_directory))
        debug_directory.mkdir()
        self._current_debug_directory = debug_directory
        return self._current_debug_directory

    def set_log_titles(self, log_titles):
        """
        Set the column titles for `loss.csv`.

        If `log_titles` is a list, each element is written in its own column.
        """
        self.log(log_titles, printout=False)

    def log(self, values, printout=False):
        """
        Add a new log entry to `loss.csv`.

        A new row is added to the spreadsheet and populated with the
        contents of `values`. If `values` is a list, each element is
        written in its own column.

        Note that this is unbuffered, so the values will be written
        immediately, and even without a call to `save()`.

        Warning: If the string verion of any value contains a comma,
        this will separate that value over two columns.
        """
        if self.readonly:
            raise ReadOnlyError(self._name)
        if not isinstance(values, list):
            values = [values]
        line = ', '.join(str(v) for v in values)
        with self.paths['loss'].open(mode='a') as f:
            f.writelines(line + '\n')
        if printout:
            print('log: {}'.format(line))

    def set_optimizer_params(self, learning_rate, weight_decay):
        if self.readonly:
            raise ReadOnlyError(self._name)
        self.state_vars['lr'] = learning_rate
        self.state_vars['wd'] = weight_decay
        for param_group in self._optimizer.param_groups:
            param_group['lr'] = learning_rate
            param_group['weight_decay'] = weight_decay

    def adjust_learning_rate(self):
        """
        Sets the learning rate to the initial learning rate decayed by
        `gamma` every `gamma_step` epochs.

        `gamma`, `gamma_step`, and the current epoch are pulled from the
        archive's `state_vars` dictionary.
        """  # TODO: reformulate as params
        if self.readonly:
            raise ReadOnlyError(self._name)
        epoch = self._state_vars.epoch
        gamma = self._state_vars.gamma
        gamma_step = self._state_vars.gamma_step
        self._state_vars.lr = (self._state_vars.start_lr
                               * (gamma ** (epoch // gamma_step)))
        for param_group in self._optimizer.param_groups:
            param_group['lr'] = self._state_vars.lr

    def visualize_loss(self, *columns, average_over=100):
        """
        Save a plot of the learning curves
        """
        data = pd.read_csv(self.paths['loss'], sep='\\s*,\\s*',
                           encoding='ascii', engine='python',
                           comment='#')[list(columns)]
        # ensure averaging window is reasonable
        if average_over > len(data.index) // 10 + 1:
            average_over = len(data.index) // 10 + 1
        if average_over < 1:
            average_over = 1
        data = data.dropna(axis=1, how='all').interpolate()
        if data.empty:
            return
        data = data.rolling(window=average_over).mean()
        data.plot(title='Training loss for {}'.format(self._name))
        with self.paths['plot'].open('wb') as f:
            plt.savefig(f)
        cp(self.paths['plot'], self._current_debug_directory)


def set_seed(seed):
    """
    Seeds all the random number genertators used.
    If `seed` is not None, the seeding is deterministic and reproducible.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.backends.cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which may slow down training.')


def get_random_generator_state():
    """
    Returns a tuple of states of the random generators used in training
    """
    python_state = random.getstate()
    numpy_state = np.random.get_state()
    torch_state = torch.get_rng_state()
    return python_state, numpy_state, torch_state


def set_random_generator_state(state):
    """
    Resets the random generators to the given state.
    Useful when resuming training.

    The state should be a state generated by calling
    `get_random_generator_state()`
    """
    python_state, numpy_state, torch_state = state
    random.setstate(python_state)
    np.random.set_state(numpy_state)
    torch.set_rng_state(torch_state)
    if not torch.backends.cudnn.deterministic:
        warnings.warn('Resetting random state might not seed GPU correctly.')
        torch.backends.cudnn.deterministic = True


class FileLog:
    """
    A file-like object that writes both to the terminal and to
    a specified file.

    `terminal_out` should be either `sys.stdout` or `sys.stderr`
    """

    def __init__(self, terminal_out, file):
        self.terminal_out = terminal_out
        self.file = file.open('a')

    def write(self, message):
        self.terminal_out.write(message)
        self.file.write(message)
        self.file.flush()

    def flush(self):
        self.terminal_out.flush()
        self.file.flush()


class dotdict(dict):
    """Allow accessing dict elements with dot notation"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


class ReadOnlyError(AttributeError):
    def __init__(self, name):
        message = ('Cannot modify the archive since it was opened as '
                   'read-only. If modifying is necessary, open it with '
                   '`ModelArchive("{}", readonly=False)`.'.format(name))
        super().__init__(message)


def warn_change(param_name, before, now):
    """
    Warns the user of a discrepancy in the stored archive, and asks
    for affirmation to continue.
    """
    warnings.warn('The {} has been changed since '
                  'this model was last saved.\n'
                  'Before: {}\n'
                  'Now: {}\n'
                  'If this is not intentional, then something may have gone '
                  'wrong. Proceeding may overwrite the appropriate value '
                  'in the archive.\n'
                  'Would you like to proceed?  [y/N]'
                  .format(param_name, before, now))
    if input().lower() not in {'yes', 'y'}:
        print('Exiting')
        sys.exit()
    print('OK, proceeding...')
