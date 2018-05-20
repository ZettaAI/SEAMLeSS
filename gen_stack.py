from cv_sampler import Sampler
import numpy as np
import h5py
import argparse
import sys
import ast

name = sys.argv[1]

parser = argparse.ArgumentParser()
parser.add_argument('name')
parser.add_argument('--count', type=int)
parser.add_argument('--test', action='store_true')
parser.add_argument('--mip', type=int, default=5)
parser.add_argument('--stack_height', type=int, default=50)
parser.add_argument('--dim', type=int, default=1152)
parser.add_argument('--coords', type=str, default=None)
args = parser.parse_args()
is_test = args.test
N = args.count
print('Parameters:', is_test, N)
dim = args.dim
mip = args.mip
stack_height = args.stack_height
#sampler = Sampler(source='gs://neuroglancer/pinky40_v11/image', dim=dim, mip=mip, height=stack_height)
#sampler = Sampler(source='gs://neuroglancer/pinky40_alignment/prealigned', dim=dim, mip=mip, height=stack_height)
sampler = Sampler(source='gs://neuroglancer/basil_v0/raw_image', dim=dim, mip=mip, height=stack_height)

def get_chunk(coords=None, coords_=None):    
    chunk = None
    if coords is None:
        while chunk is None:
            chunk, coords = sampler.random_sample(train=not is_test)
            if chunk is None:
                print('None')
                continue
    else:
        chunk = sampler.chunk_at_global_coords(coords, coords_)
    return chunk, coords

archived_coords = None
if args.coords is not None:
    with open(args.coords) as coord_file:
        archived_coords = ast.literal_eval(coord_file.read())

if archived_coords is not None:
    print('Overwriting argument N ({}) with length of archived coordinates ({})'.format(N, len(archived_coords)))
    N = len(archived_coords)
    print('Using fixed coordinates: {}'.format(archived_coords))

coord_record = []
dataset = np.empty((N, stack_height, dim, dim))

for i in range(N):
    coords = None
    if archived_coords is not None:
        ac = archived_coords[i]
        coords = (ac[0], ac[1], ac[2])
        coords_ = (ac[0] + ac[3], ac[1] + ac[3], ac[2] + ac[4])
    chunk, coords = get_chunk(coords, coords_)
    coord_record.append(coords)
    dataset[i,:,:,:] = np.transpose(chunk, (2,0,1))
    print(i)

record_file = open(args.name + '_' + ('test' if is_test else 'train') + '_mip' + str(mip) + 'coords.txt', 'w')
record_file.write(str(coord_record))
record_file.close()

h5f = h5py.File(args.name + '_' + ('test' if is_test else 'train') + '_mip' + str(mip) + '.h5', 'w')
h5f.create_dataset('main', data=dataset)
h5f.close()
