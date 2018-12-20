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
  args.serial_operation = True
  a = get_aligner(args)
  bbox = get_bbox(args)

  z_range = range(args.bbox_start[2], args.bbox_stop[2])
  a.dst[0].add_composed_cv(args.bbox_start[2], inverse=False)
  field_k = a.dst[0].get_composed_key(args.bbox_start[2], inverse=False)
  field_cv= a.dst[0].for_read(field_k)
  dst_cv = a.dst[0].for_write('dst_img')
  z_offset = 1
  uncomposed_field_cv = a.dst[z_offset].for_read('field')

  mip = args.mip

  if args.align_start:
    copy_range = z_range[0:1]
    align_range = z_range[1:]
  else:
    copy_range = z_range[0:3]
    align_range = z_range[3:]

  # copy first section
  for z in copy_range:
    print('Copying z={0}'.format(z))
    a.copy_section(z, dst_cv, z, bbox, mip)
    a.downsample(dst_cv, z, bbox, a.render_low_mip, a.render_high_mip)
  # align without vector voting
  for z in align_range:
    print('Aligning without vector voting z={0}'.format(z))
    src_z = z
    tgt_z = z-1
    a.compute_section_pair_residuals(src_z, tgt_z, bbox)
    a.render_section_all_mips(src_z, uncomposed_field_cv, src_z,
                              dst_cv, src_z, bbox, mip)