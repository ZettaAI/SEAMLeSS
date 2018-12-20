import sys
import torch
from args import get_argparser, parse_args, get_aligner, get_bbox 
from os.path import join

if __name__ == '__main__':
  parser = get_argparser()
  parser.add_argument('--align_start', 
    help='align without vector voting the 2nd & 3rd sections, otherwise copy them', action='store_true')
  args = parse_args(parser)
  args.tgt_path = join(args.dst_path, 'image')
  # only compute matches to previous sections
  args.forward_matches_only = True
  a = get_aligner(args)
  bbox = get_bbox(args)

  z_range = range(args.bbox_start[2], args.bbox_stop[2])
  a.dst[0].add_composed_cv(args.bbox_start[2], inverse=False)
  field_k = a.dst[0].get_composed_key(args.bbox_start[2], inverse=False)
  field_cv= a.dst[0].for_read(field_k)
  dst_cv = a.dst[0].for_write('dst_img_1')
  print("dst_cv", dst_cv)
  z_offset = 1
  uncomposed_field_cv = a.dst[z_offset].for_read('field')

  vector_mip = args.mip
  image_mip = args.render_low_mip
  composed_range = z_range
  for z in composed_range:
    a.render_to_low_mip(z, field_cv, z, dst_cv, z, bbox, image_mip, vector_mip)
