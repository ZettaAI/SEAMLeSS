from concurrent.futures import ProcessPoolExecutor
import taskqueue
from taskqueue import TaskQueue, GreenTaskQueue

import sys
import torch
import json
import math
import csv
from time import time, sleep
from args import get_argparser, parse_args, get_aligner, get_bbox, get_provenance
from os.path import join
from cloudmanager import CloudManager
from itertools import compress
from tasks import run
from boundingbox import BoundingBox
import numpy as np

def print_run(diff, n_tasks):
  if n_tasks > 0:
    print (": {:.3f} s, {} tasks, {:.3f} s/tasks".format(diff, n_tasks, diff / n_tasks))

def make_range(block_range, part_num):
    rangelen = len(block_range)
    if(rangelen < part_num):
        srange =1
        part = rangelen
    else:
        part = part_num
        srange = rangelen//part
    range_list = []
    for i in range(part-1):
        range_list.append(block_range[i*srange:(i+1)*srange])
    range_list.append(block_range[(part-1)*srange:])
    return range_list
 
def ranges_overlap(a_pair, b_pair):
  a_start, a_stop = a_pair
  b_start, b_stop = b_pair
  return ((b_start <= a_start and b_stop >= a_start) or
         (b_start >= a_start and b_stop <= a_stop) or
         (b_start <= a_stop  and b_stop >= a_stop))


if __name__ == '__main__':
  parser = get_argparser()
  parser.add_argument('--affine_lookup', type=str,
                      help='path to csv of affine transforms indexed by section')
  parser.add_argument('--model_lookup', type=str,
    help='relative path to CSV file identifying model to use per z range')
  parser.add_argument('--z_range_path', type=str, 
    help='path to csv file with list of z indices to use')
  parser.add_argument('--src_path', type=str)
  parser.add_argument('--src_mask_path', type=str, default='',
    help='CloudVolume path of mask to use with src images; default None')
  parser.add_argument('--src_mask_mip', type=int, default=8,
    help='MIP of source mask')
  parser.add_argument('--src_mask_val', type=int, default=1,
    help='Value of of mask that indicates DO NOT mask')
  parser.add_argument('--dst_path', type=str)
  parser.add_argument('--mip', type=int)
  parser.add_argument('--z_start', type=int)
  parser.add_argument('--z_stop', type=int)
  parser.add_argument('--max_mip', type=int, default=9)
  parser.add_argument('--tgt_radius', type=int, default=3,
    help='int for number of sections to include in vector voting')
  parser.add_argument('--pad', 
    help='the size of the largest displacement expected; should be 2^high_mip', 
    type=int, default=2048)
  parser.add_argument('--block_size', type=int, default=10)
  parser.add_argument('--restart', type=int, default=0)
  parser.add_argument('--use_sqs_wait', action='store_true',
    help='wait for SQS to return that its queue is empty; incurs fixed 30s for initial wait')
  args = parse_args(parser)
  # Only compute matches to previous sections
  args.serial_operation = True
  a = get_aligner(args)
  provenance = get_provenance(args)
  chunk_size = 1024

  # Simplify var names
  mip = args.mip
  max_mip = args.max_mip
  pad = args.pad
  src_mask_val = args.src_mask_val
  src_mask_mip = args.src_mask_mip

  # Create CloudVolume Manager
  cm = CloudManager(args.src_path, max_mip, pad, provenance, batch_size=1,
                    size_chunk=chunk_size, batch_mip=mip)
  
  # compile bbox & model lookup per z index
  bbox_lookup = {}
  model_lookup = {}
  with open(args.model_lookup) as f:
    reader = csv.reader(f, delimiter=',')
    for k, r in enumerate(reader):
       if k != 0:
         x_start = int(r[0])
         y_start = int(r[1])
         z_start = int(r[2])
         x_stop  = int(r[3])
         y_stop  = int(r[4])
         z_stop  = int(r[5])
         bbox_mip = int(r[6])
         model_path = join('..', 'models', r[7])
         bbox = BoundingBox(x_start, x_stop, y_start, y_stop, bbox_mip, max_mip)
         for z in range(z_start, z_stop):
           bbox_lookup[z] = bbox 
           model_lookup[z] = model_path

  affine_lookup = None
  if args.affine_lookup:
    affine_lookup = {}
    with open(args.affine_lookup) as f:
      affine_list = json.load(f)
      for aff in affine_list:
        z = aff['z']
        affine_lookup[z] = np.array(aff['transform'])

  # Compile ranges
  block_range = range(args.z_start, args.z_stop, args.block_size)
  even_odd_range = [i % 2 for i in range(len(block_range))]
  if args.z_range_path:
    print('Compiling z_range from {}'.format(args.z_range_path))
    block_endpoints = range(args.z_start, args.z_stop+args.block_size, args.block_size)
    block_pairs = list(zip(block_endpoints[:-1], block_endpoints[1:]))
    tmp_block_range = []
    tmp_even_odd_range = []
    with open(args.z_range_path) as f:
      reader = csv.reader(f, delimiter=',')
      for k, r in enumerate(reader):
         if k != 0:
           z_pair = int(r[0]), int(r[1])
           print('Filtering block_range by {}'.format(z_pair))
           block_filter = [ranges_overlap(z_pair, b_pair) for b_pair in block_pairs]
           affected_blocks = list(compress(block_range, block_filter))
           affected_even_odd = list(compress(even_odd_range, block_filter))
           print('Affected block_starts {}'.format(affected_blocks))
           tmp_block_range.extend(affected_blocks)
           tmp_even_odd_range.extend(affected_even_odd)
    block_range = tmp_block_range
    even_odd_range = tmp_even_odd_range

  print('block_range {}'.format(block_range))
  print('even_odd_range {}'.format(even_odd_range))

  overlap = args.tgt_radius
  full_range = range(args.block_size + overlap)

  copy_range = full_range[overlap-1:overlap]
  serial_range = full_range[:overlap-1][::-1]
  vvote_range = full_range[overlap:]
  copy_field_range = range(overlap, args.block_size+overlap)
  broadcast_field_range = range(overlap-1, args.block_size+overlap)

  serial_offsets = {serial_range[i]: i+1 for i in range(overlap-1)}
  vvote_offsets = [-i for i in range(1, overlap+1)]

  print('copy_range {}'.format(copy_range))
  print('serial_range {}'.format(serial_range))
  print('vvote_range {}'.format(vvote_range))
  print('serial_offsets {}'.format(serial_offsets))
  print('vvote_offsets {}'.format(vvote_offsets))

  # Create src CloudVolumes
  src = cm.create(args.src_path, data_type='uint8', num_channels=1,
                     fill_missing=True, overwrite=False)
  src_mask_cv = None
  tgt_mask_cv = None
  if args.src_mask_path:
    src_mask_cv = cm.create(args.src_mask_path, data_type='uint8', num_channels=1,
                               fill_missing=True, overwrite=False)
    tgt_mask_cv = src_mask_cv

  if src_mask_cv != None:
      src_mask_cv = src_mask_cv.path
  if tgt_mask_cv != None:
      tgt_mask_cv = tgt_mask_cv.path

  # Create dst CloudVolumes for odd & even blocks, since blocks overlap by tgt_radius 
  dsts = {}
  block_types = ['even', 'odd']
  for i, block_type in enumerate(block_types):
    dst = cm.create(join(args.dst_path, 'image_blocks', block_type),
                    data_type='uint8', num_channels=1,compress=None,
                    fill_missing=True, overwrite=True)
    dsts[i] = dst

  # Create field CloudVolumes
  serial_fields = {}
  for z_offset in serial_offsets.values():
    serial_fields[z_offset] = cm.create(join(args.dst_path, 'field', str(z_offset)), 
                                  data_type='int16', num_channels=2,
                                  fill_missing=True, overwrite=True)
  pair_fields = {}
  for z_offset in vvote_offsets:
    pair_fields[z_offset] = cm.create(join(args.dst_path, 'field', str(z_offset)), 
                                      data_type='int16', num_channels=2,
                                      fill_missing=True, overwrite=True).path
  vvote_field0 = cm.create(join(args.dst_path, 'field', 'vvote_{}'.format(overlap)), 
                          data_type='int16', num_channels=2,
                          fill_missing=True, overwrite=True)
  vvote_field1 = cm.create(join(args.dst_path, 'field', 'vvote_uncompressed{}'.format(overlap)), 
                          data_type='int16', num_channels=2, compress=False,
                          fill_missing=True, overwrite=True)

  ###########################
  # Serial alignment script #
  ###########################
  # check for restart
  copy_range = [r for r in copy_range if r >= args.restart]
  serial_range = [r for r in serial_range if r >= args.restart]
  vvote_range = [r for r in vvote_range if r >= args.restart]
  
  # Copy first section
  
  def remote_upload(tasks):
      with GreenTaskQueue(queue_name=args.queue_name) as tq:
          tq.insert_all(tasks)  

#  # Align without vector voting
#  # field need to in float since all are relative value
  rows = 80  #14
  block_start = args.z_start
  super_chunk_len = 15
  overlap_chunks = 2 * (super_chunk_len -1)
  #chunk_grid = a.get_chunk_grid(cm, bbox, mip, overlap_chunks, rows, pad)
  chunk_grid = a.get_chunk_grid(cm, bbox, mip, 0, rows, pad)
  print("copy range ", copy_range)
  #print("---- len of chunks", len(chunk_grid), "orginal bbox", bbox.stringify(0))
  vvote_range_small = vvote_range
  #vvote_range_small = vvote_range[:super_chunk_len-overlap+1]
  #vvote_subblock = range(vvote_range_small[-1]+1, vvote_range[-1]+1,
  #                          super_chunk_len)
  print("--------overlap is ", overlap, "vvote_range is ", "vvote_range_small",
        vvote_range_small)
  dst = dsts[0]
  #for i in vvote_subblock:
  #    print("*****>>> vvote subblock is ", i)
  for i in  chunk_grid:
      print("--------grid size is ", i.stringify(0, mip=mip))

  for i in range(len(chunk_grid)):
      chunk = chunk_grid[i]
      if(len(chunk_grid) == 1):
          head_crop = False
          end_crop = False
      else:
          if i == 0:
              head_crop = False
              end_crop = True
          elif i == (len(chunk_grid) -1):
              head_crop = True
              end_crop = False
          else:
              head_crop = True
              end_crop = True

      if head_crop == False and end_crop == True:
          final_chunk = a.crop_chunk(chunk, mip, pad,
                                     chunk_size*(super_chunk_len-1)+pad,
                                     pad, pad)
      elif head_crop and end_crop:
          final_chunk = a.crop_chunk(chunk, mip, chunk_size*(super_chunk_len-1)+pad,
                                     chunk_size*(super_chunk_len-1)+pad,
                                     pad, pad)
      elif head_crop and end_crop == False:
          final_chunk = a.crop_chunk(chunk, mip, chunk_size*(super_chunk_len-1)+pad,
                                     pad, pad, pad)
      else:
          final_chunk = a.crop_chunk(chunk, mip, pad,
                                     pad, pad, pad)
      print("----------------------- head crop ", head_crop, " end_crop",
            end_crop)

      print("<<<<<<init chunk size is ", chunk.stringify(0, mip=mip),
            "final_chunk is ", final_chunk.stringify(0, mip=mip))
      #image_list, chunk = 
      a.get_aligned_section(src, block_start, copy_range[0],
                          serial_range, serial_offsets,
                          vvote_field0, vvote_field1, model_lookup,
                          chunk, mip, pad, chunk_size,
                          head_crop, end_crop, final_chunk)

      #write_image = []
      #for _ in image_list:
      #    write_image.append(True)
      #print("============================ start vector voting")
      ## align with vector voting
      #vvote_way = args.tgt_radius
      #a.process_super_chunk_vvote(src, block_start, vvote_range_small, dst,
      #                            model_lookup, vvote_way, image_list,
      #                            write_image, chunk,
      #                            mip, pad, chunk_size, super_chunk_len,
      #                            vvote_field,
      #                            head_crop, end_crop, final_chunk,
      #                            mask_cv=src_mask_cv, mask_mip=src_mask_mip,
      #                            mask_val=src_mask_val)

#  print("Only vector voting ------------>>")
#  vvote_subblock = [vvote_range[0]]
#  for vvote_start in vvote_subblock:
#      #if vvote_start + super_chunk_len <= vvote_range[-1]+1:
#      #    end_range = vvote_start + super_chunk_len
#      #else:
#      #    end_range = vvote_range[-1] +1;
#      #    super_chunk_len = vvote_range[-1] +1 - vvote_start;
#
#      #print("<<<<<<<<-------start ", vvote_start, " end range ", end_range,
#      #      "vvote range is ", vvote_range )
#      #overlap_chunks = 2 * (super_chunk_len -1)
#      overlap_chunks =0
#      chunk_grid = a.get_chunk_grid(cm, bbox, mip, overlap_chunks, rows, pad)
#      #a.random_read(cm, src, bbox, mip, pad, block_start+vvote_start)
#      for j in range(len(chunk_grid)):
#          chunk = chunk_grid[j]
#          print("<<<<<<init chunk size is ", chunk.stringify(0, mip=mip))
#          if(len(chunk_grid) == 1):
#              head_crop = False
#              end_crop = False
#          else:
#              if j == 0:
#                  head_crop = False
#                  end_crop = True
#              elif j == (len(chunk_grid) -1):
#                  head_crop = True
#                  end_crop = False
#              else:
#                  head_crop = True
#                  end_crop = True
#          if head_crop == False and end_crop == True:
#              final_chunk = a.crop_chunk(chunk, mip, pad,
#                                         chunk_size*(super_chunk_len-1)+pad,
#                                         pad, pad)
#          elif head_crop and end_crop:
#              final_chunk = a.crop_chunk(chunk, mip, chunk_size*(super_chunk_len-1)+pad,
#                                         chunk_size*(super_chunk_len-1)+pad,
#                                         pad, pad)
#          elif head_crop and end_crop == False:
#              final_chunk = a.crop_chunk(chunk, mip, chunk_size*(super_chunk_len-1)+pad,
#                                         pad, pad, pad)
#          else:
#              final_chunk = a.crop_chunk(chunk, mip, pad,
#                                         pad, pad, pad)
#          vvote_way = args.tgt_radius
#          image_list = []
#          write_image = []
#          print("head_crop is", head_crop ," end_crop is", end_crop)
#          for i in range(vvote_way):
#              start_im = time()
#              # modify dst to src for profiling purpose, remember to change
#              # back
#              image_for_vv = a.load_part_image(src,
#                                                  block_start+vvote_start-i,
#                                  chunk, mip, mask_cv=src_mask_cv,
#                                  mask_mip=src_mask_mip, mask_val=src_mask_val,
#                                  to_tensor=False)
#              image_list.insert(0,image_for_vv)
#              #modify the write image for profiling purpose
#              #write_image.insert(0, False)
#              write_image.insert(0, True)
#              end_im = time()
#              print(">>>>>>>>>>>>>>>>>read image time:", end_im - start_im)
#              #a.save_image(image_for_vv, dst, z-(vvote_way-i-1),
#              #         chunk, mip, to_uint8=False)
#              #print(">>>>>>>>>>>>>>>>>write image time:",time() - end_im)
#          #vvote_range_small = range(vvote_start, end_range)
#          vvote_range_small = vvote_range
#          print("vvote_range_small is", vvote_range_small)
#          a.process_super_chunk_vvote(src, block_start, vvote_range_small, dst,
#                                      model_lookup, vvote_way, image_list,
#                                      write_image, chunk,
#                                      mip, pad, chunk_size, super_chunk_len,
#                                      vvote_field,
#                                      head_crop, end_crop, final_chunk,
#                                      mask_cv=src_mask_cv, mask_mip=src_mask_mip,
#                                      mask_val=src_mask_val)
#
#  for offset in vvote_large_range:
#      first_chunk = True
#      for chunk in chunk_grid:
#          for block_offset in vvote_subrange:
#              dst = dsts[0]
#              z = block_start + block_offset
#              bbox = bbox_lookup[z]
#              model_path = model_lookup[z]
#              vvote_way = args.tgt_radius
#              src_image = a.load_part_image(src, z, chunk, mip, mask_cv=src_mask_cv,
#                                          mask_mip=src_mask_mip,
#                                          mask_val=src_mask_val)
#              for i in image_list:
#                  print("************shape of image", i.shape)
#              chunk = a.adjust_chunk(chunk, mip, chunk_size, first_chunk=first_chunk)
#              image, dst_field = a.new_vector_vote(model_path, src_image, image_list, chunk_size, pad,
#                               vvote_way, mip, inverse=False, serial=True)
#              a.save_image(image_list[0], dst, mip, z-vvote_way, to_uint8=False)
#              del image_list[0]
#              image_list.append(image)
#              dst_field = dst_field.cpu().numpy() * ((chunk_size+2*pad)/ 2) * (2**mip)
#              a.save_field(dst_field, vvote_field, z, chunk, mip, relative=False,
#                           as_int16=True)
#          first_chunk = False
#
#
#    ptask = []
#    start = time()
#    for irange, ieven_odd in zip(range_list, even_odd_list):
#        ptask.append(RenderTaskIteratorII(irange, ieven_odd))
#    
#    with ProcessPoolExecutor(max_workers=a.threads) as executor:
#        executor.map(remote_upload, ptask)
#   
#    end = time()
#    diff = end - start
#    print("Sending Render Tasks use time:", diff)
#    print('Run rendering')
#
#    start = time()
#    # wait 
#    a.wait_for_sqs_empty()
#    end = time()
#    diff = end - start
#    print("Executing Rendering use time:", diff)
#
