#!/bin/bash
#SBATCH --job-name=align
#SBATCH --nodes=3
#SBATCH --output=bufferlog.txt
#SBATCH --cpus-per-task=12
#SBATCH --gres=gpu:4
#SBATCH -t 72:00:00
#SBATCH --mem 200G
# sends mail when process begins, and
# when it ends. Make sure you difine your email
# address
#SBATCH --mail-type=begin
#SBATCH --mail-type=end
#SBATCH --mail-user=runzhey@cs.princeton.edu

export PATH="/home/runzhey/miniconda3/bin:$PATH"
export LANGUAGE=en_US.UTF-8
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
source activate rllab3

cd /home/runzhey/SEAMLeSS/
python training/train.py --num_workers 12 --gpu_ids 0,1,2,3,4,5,6,7,8,9,10,11 start tiger_12 --training_set /home/runzhey/training_data/minnie_mip4_annotated_major_folds_curriculum_45x6x1536.h5 --validation_set /home/runzhey/training_data/v1_100slice_minnie_6144_train_mip2_annotated.h5 --height 5 --seed 5432 -u --lr 0.000005 --lambda1 300000 --plan all --encodings

