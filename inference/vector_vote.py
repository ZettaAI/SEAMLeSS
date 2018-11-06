import torch
from torch import matmul, pow
from torch.nn.functional import softmax
from cloudvolume.lib import Bbox, Vec
import math

import util
import argparse

def dist(U, V):
  D = U - V
  N = pow(D, 2)
  return pow(torch.sum(N, 3), 0.5).unsqueeze(0)

def get_diffs(fields):
  diffs = []
  for i in range(len(fields)):
    for j in range(i+1, len(fields)):
      diffs.append(dist(fields[i], fields[j]))
  return torch.cat(diffs, dim=0)

def weight_diffs(diffs, T=1):
  return softmax(-diffs / T, dim=0)

def compile_field_weights(W):
  m = W.shape[0]
  n = int((1 + math.sqrt(1 + 8*m)) / 2)
  C = torch.zeros((n,) +  W.shape[1:])
  C = C.to(device=W.device)
  k = 0
  for i in range(n):
    for j in range(i+1, n):
      C[i,...] += W[k,...]
      C[j,...] += W[k,...]
      k += 1  
  return C / (n-1)

def weighted_sum_fields(weights, fields):
  """Created weighted sum of a list of fields
  
  Args:
     weights: tensor with batch-size N
     fields: N-size list of tensors
  """
  field = torch.cat(fields, dim=0)
  field = field.permute(2,1,0,3)
  weights = weights.permute(3,2,1,0)
  # print('weighted_sum_fields weights.shape {0}'.format(weights.shape))
  # print('weighted_sum_fields field.shape {0}'.format(field.shape))
  return matmul(weights, field).permute(2,1,0,3)

def vector_vote(fields, T=1):
  diffs = get_diffs(fields)
  diff_weights = weight_diffs(diffs, T=T)
  field_weights = compile_field_weights(diff_weights)
  return weighted_sum_fields(field_weights, fields)

if __name__ == '__main__':

  parser = argparse.ArgumentParser(
              description='Combine vector fields based on voting.')
  parser.add_argument('--field_paths', type=str, nargs='+', 
    help='List of CloudVolume paths to images')
  parser.add_argument('--weight_path', type=str,
    help='CloudVolume path where to write weights')
  parser.add_argument('--dst_path', type=str,
    help='CloudVolume path where output image written')
  parser.add_argument('--mip', type=int,
    help='MIP level of images to be used in evaluation')
  parser.add_argument('--temperature', type=int, default=1,
    help='softmax temperature')
  parser.add_argument('--bbox_start', nargs=3, type=int,
    help='bbox origin, 3-element int list')
  parser.add_argument('--bbox_stop', nargs=3, type=int,
    help='bbox origin+shape, 3-element int list')
  parser.add_argument('--bbox_mip', type=int, default=0,
    help='MIP level at which bbox_start & bbox_stop are specified')
  parser.add_argument('--disable_cuda', action='store_true', help='Disable CUDA')
  args = parser.parse_args()

  bbox = Bbox(args.bbox_start, args.bbox_stop)
  args.device = None
  if not args.disable_cuda and torch.cuda.is_available():
    args.device = torch.device('cuda')
  else:
    args.device = torch.device('cpu')

  srcs = []
  for path in args.field_paths:
    srcs.append(util.get_field_cloudvolume(path, mip=args.mip))
  dst = util.create_field_cloudvolume(args.dst_path, srcs[0][0].info, 
                                     args.mip, args.mip)

  wts = util.create_cloudvolume(args.weight_path, srcs[0][0].info, 
                                     args.mip, args.mip)
  wts.info['num_channels'] = 3
  wts.commit_info()

  bbox = srcs[0][0].bbox_to_mip(bbox, args.bbox_mip, args.mip)
  fields = []
  for src in srcs:
    print('Loading field from {0}'.format(src[0].path))
    f = util.get_field(src, bbox)
    print('field shape {0}'.format(f.shape)) 
    fields.append(f)

  print('get_diffs')
  diffs = get_diffs(fields)
  print('diffs.shape {0}'.format(diffs.shape))
  print('weight_diffs')
  diff_weights = weight_diffs(diffs, T=args.temperature)
  print('diff_weights.shape {0}'.format(diff_weights.shape))
  field_weights = compile_field_weights(diff_weights)
  print('field_weights.shape {0}'.format(field_weights.shape))
  print('weighted_sum_fields')
  field = weighted_sum_fields(field_weights, fields)
  print('diff_weights.shape {0}'.format(diff_weights.shape))
  util.save_image(wts, bbox, util.to_numpy(field_weights.permute(1,0,3,2)))
  util.save_field(dst, bbox, util.field_to_numpy(field))
