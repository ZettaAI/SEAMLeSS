from aligner import Aligner, BoundingBox
from link_builder import ng_link
import argparse

def get_argparser():
  parser = argparse.ArgumentParser()
  parser.add_argument('--model_path', type=str)
  parser.add_argument('--max_displacement', 
    help='the size of the largest displacement expected; should be 2^high_mip', 
    type=int, default=2048)
  parser.add_argument('--crop', type=int, default=384)
  parser.add_argument('--mip', type=int)
  parser.add_argument('--src_path', type=str,
    help='CloudVolume path of images to be warped')
  parser.add_argument('--tgt_path', type=str,
    help='CloudVolume path of images to align against; default: dst_path')
  parser.add_argument('--dst_path', type=str,
    help='CloudVolume path of rendered images')
  parser.add_argument('--src_mask_path', type=str, default='',
    help='CloudVolume path of mask to use with src images; default None')
  parser.add_argument('--src_mask_mip', type=int, default=8,
    help='MIP of source mask')
  parser.add_argument('--src_mask_val', type=int, default=1,
    help='Value of of mask that indicates DO NOT mask')
  parser.add_argument('--tgt_mask_path', type=str, default='',
    help='CloudVolume path of mask to use with tgt images; default None')
  parser.add_argument('--tgt_mask_mip', type=int, default=8,
    help='MIP of target mask')
  parser.add_argument('--tgt_mask_val', type=int, default=1,
    help='Value of of mask that indicates DO NOT mask')
  parser.add_argument('--size', type=int, default=8)
  parser.add_argument('--skip', type=int, default=0)
  parser.add_argument('--render_mip', type=int)
  parser.add_argument('--should_contrast', action='store_true')
  parser.add_argument('--num_targets', type=int, default=1)
  parser.add_argument('--max_mip', type=int, default=9)
  parser.add_argument('--bbox_start', nargs=3, type=int,
    help='bbox origin, 3-element int list')
  parser.add_argument('--bbox_stop', nargs=3, type=int,
    help='bbox origin+shape, 3-element int list')
  parser.add_argument('--bbox_mip', type=int, default=0,
    help='MIP level at which bbox_start & bbox_stop are specified')
  parser.add_argument('--tgt_radius', type=int, default=1,
    help='Radius of z sections to include in multi-match')
  parser.add_argument('--disable_flip_average', 
    help='disable flip averaging', 
    action='store_true')
  parser.add_argument('--old_upsample', 
    help='revert to the old pytorch upsampling (using align_corners=True)',
    action='store_true')
  parser.add_argument('--old_vectors', 
    help='vectors in old convention, -1 & 1 refer border pixel centers instead of image edges.',
    action='store_true')
  parser.add_argument('--ignore_field_init', 
    help='do not initialize the field cloudvolume (already exists)',
    action='store_true')
  parser.add_argument('--write_intermediaries', 
    help='write encodings, residuals, & cumulative residuals to cloudvolumes', 
    action='store_true')
  parser.add_argument('--upsample_residuals', 
    help='upsample residuals & cum_residuals when writing intermediaries; requires --write_intermediaries flag', 
    action='store_true')
  parser.add_argument('--p_render', help='parallel rendering among all slices', action='store_true')
  return parser

def parse_args(parser, arg_string=''):
  if arg_string:
    args = parser.parse_args(arg_string)
  else:
    args = parser.parse_args()
  
  args.tgt_path = args.tgt_path if args.tgt_path else args.src_path
  args.mip_range = (args.mip, args.mip)
  args.high_mip_chunk = (1024, 1024)
  args.top_skip = 0
  return args

def get_aligner(args):
  """Create Aligner object from args
  """
  print('model_path: {0}'.format(args.model_path))
  print('src_path: {0}'.format(args.src_path))
  print('dst_path: {0}'.format(args.dst_path))
  print('Coordinates:', args.bbox_start, args.bbox_stop)
  print('Mip:', args.mip)
  print('Contrast:', args.should_contrast)
  print('Max mip:', args.max_mip)
  print('NG link:', ng_link('dst', 'precomputed://' + args.dst_path +'/image', 'src', 'precomputed://' + args.src_path, (args.bbox_start[0]+args.bbox_stop[0])//2, (args.bbox_start[1]+args.bbox_stop[2])//2, args.bbox_start[2]))
  return Aligner(**vars(args))

def get_bbox(args):
  """Create BoundingBox object from args
  """
  # interleave coords by flattening
  coords = [x for t in zip(args.bbox_start[:2], args.bbox_stop[:2]) for x in t]
  return BoundingBox(*coords, mip=0, max_mip=args.max_mip)

