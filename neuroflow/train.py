from __future__ import print_function
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision
from torchvision import datasets, transforms
from torch.autograd import Variable

import time
import numpy as np
import cavelab as cl
from model import Pyramid, Xmas
from data import Data
from loss import loss
from util import get_identity, name, visualize, log_param_mean
from tensorboardX import SummaryWriter
import json

if not torch.cuda.is_available():
    raise ValueError("Cuda is not available")

debug = False

#TODO smoothness penalty seperately

def freeze(model, level=0):
    print('freeze', level)
    if level>len(model.G_level)-1:
        return
    for param in model.G_level[level].parameters():
        param.requires_grad = False

def freeze_all(model, besides=0):
    levels = len(model.G_level)
    for i in range(levels):
        for param in model.G_level[i].parameters():
            param.requires_grad = False

    for param in model.G_level[besides].parameters():
        param.requires_grad = True

def unfreeze(model):
    levels = len(model.G_level)
    for i in range(levels):
        for param in model.G_level[i].parameters():
            param.requires_grad = True


def train(hparams):

    # Load Data, Model, Logging
    d = Data(hparams)

    path = os.path.join('logs/'+hparams.name, hparams.version)

    if not debug:
        writer = SummaryWriter(log_dir=path)
        with open(path+'/hparams.json', 'w') as outfile:
            json.dump(hparams, outfile)
    width = hparams.features['image']['width']
    input_shape = [hparams.levels, hparams.batch_size, width, width]
    model = Xmas(levels = hparams.levels,
                 skip_levels = hparams.skip_levels,
                 shape = input_shape)

    model.cuda(device=0)

    #optimizer = optim.Adam(model.parameters(), lr=hparams.learning_rate)
    model.train()
    level = hparams.levels
    image = d.get_batch()
    for i in range(hparams.steps):
        t1 = time.time()


        xs = torch.autograd.Variable(torch.from_numpy(image).cuda(device=0), requires_grad=False)
        if i%3==0:
            level = max(0, level-1)
            if hparams.skip_levels>level:
                unfreeze(model)

            freeze_all(model, besides=max(hparams.skip_levels,level))
            params = filter(lambda x: x.requires_grad, model.parameters())
            optimizer = optim.Adam(params, lr=hparams.learning_rate)

        for j in range(1):
            optimizer.zero_grad()
            ys, Rs, rs = model(xs)

            l, mse, p1, p2 = loss(xs[:], ys[:],
                                  Rs[:], rs[:],
                                  level=level,
                                  lambda_1=hparams.lambda_1,
                                  lambda_2=hparams.lambda_2)

            l.backward(), optimizer.step()
        t2 = time.time()

        print(i, 'loss',  str(l.data[0])[:4],
                          str(p1.data[0])[:4],
                          str(t2-t1)[:4]+"s")

        if i%hparams.log_iterations==0 or debug: # Takes 4s
            t3 = time.time()

            if debug:
                j = 1
                k = 0
                cl.visual.save(xs[j,k+1].data.cpu().numpy(), 'dump/image')
                cl.visual.save(ys[j,k].data.cpu().numpy(), 'dump/pred')
                cl.visual.save(xs[j,k].data.cpu().numpy(), 'dump/target')

                rss = np.transpose(Rs[j, k].data.cpu().numpy(), (1,2,0))
                rss_2 = np.transpose(get_identity(batch_size=1, width=rss.shape[0])[0], (1,2,0))
                rss_2 -= rss
                hsv, grid = cl.visual.flow(rss)
                cl.visual.save(hsv, 'dump/grid')
                continue

            for j in range(hparams.levels):
                visualize(xs[j,1:,:,:], xs[j,:-1,:,:],
                          ys[j,:,:,:], Rs[j,:,:,:],
                          rs[j,:],
                          i, writer,
                          mip_level=j,
                          crop=(2**j))
            torch.save(model, path+'/model.pt') #0.3s
            t4 = time.time()
            print('visualize time', str(t4-t3)[:4]+"s")

        if not debug:
            writer.add_scalar('data/loss', l, i)
            writer.add_scalar('data/mse', mse, i)
            writer.add_scalar('data/p1', p1, i)
            writer.add_scalar('data/p2', p2, i)
            writer.add_scalar('data/level', level, i)

            #writer.add_scalars('data/weight_mean', log_param_mean(model), i)

    writer.close()

def test(model, test_data):
    image = np.tile((8,1,1)).astype(np.float32)
    target = np.tile(test_data[:256,:256, 4]/256.0,(8,1,1)).astype(np.float32)

    image = Variable(torch.from_numpy(image).cuda(),  requires_grad=False)
    target = Variable(torch.from_numpy(target).cuda(),  requires_grad=False)
    x = torch.stack([image, target], dim=1)

    ys, Rs, rs = model(x)

    vizuaize(image, target, ys[-1], Rs[-1], i, writer)


def getopts(argv):
    opts = {}  # Empty dictionary to store key-value pairs.
    while argv:  # While there are arguments left to parse...
        if argv[0][0] == '-':  # Found a "-name value" pair.
            opts[argv[0]] = argv[1]  # Add key and value to the dictionary.
        argv = argv[1:]  # Reduce the argument list by copying it starting from index 1.
    return opts

import sys
import os
from datetime import datetime
if __name__ == "__main__":
    hparams = cl.hparams(name="default")
    myargs = getopts(sys.argv)
    if '--debug' in myargs:
        debug = True
    if '--name' in myargs:  # Example usage.
        hparams.name = myargs['--name']
    if '--version' in myargs:
        hparams.version = myargs['--version']
    else:
        hparams.version = datetime.now().strftime('%b%d_%H-%M-%S')
    train(hparams)
