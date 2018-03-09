from __future__ import print_function
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision
from torchvision import datasets, transforms
from torch.autograd import Variable

from neuroflow.util import get_identity
import numpy as np
from model import G
from .unet import Unet

class Xmas(nn.Module):
    def __init__(self, levels=1, skip_levels=2, shape=[5,8,256,256]):
        super(Xmas, self).__init__()
        self.G_level = nn.ModuleList()
        self.levels = levels
        for i in range(skip_levels):
            self.G_level.append(G(skip=True))

        for i in range(self.levels-skip_levels):
            self.G_level.append(G())

        self.upsample = nn.Upsample(scale_factor=2, mode='bilinear')
        self.ident = get_identity(batch_size=shape[1]-1, width=shape[3])
        self.ident = Variable(torch.from_numpy(self.ident).cuda(),
                                                requires_grad=False).permute(0,2,3,1)
        self.shape = shape
        self.shape[1] -= 1
        self.r_shape = [shape[0], shape[1], 2, shape[2], shape[3]]

    def up_crop(self, upcrop): #[b-1,2,256,256]
        return self.upsample(upcrop[:,:,64:64+128,64:64+128])

    def render(self, img, Res): #[b,256,256],[b-1, 2,256,256]
        R_p = Res.permute(0,2,3,1) + self.ident
        rend = F.grid_sample(img[1:,:,:].unsqueeze(1), R_p)
        return torch.cat([img[0].unsqueeze(0), rend.squeeze(1)], dim=0) #[8,256,256]

    def compute_res(self, inp, level=0): # [b,256,256]
        # [b-1,2,256,256] Batchify
        batch = Variable(torch.zeros(inp.shape[0]-1, 2, inp.shape[-1], inp.shape[-1]).cuda())
        for j in range(inp.shape[0]-1):
            batch[j] = torch.stack([inp[j], inp[j+1]], 0)

        res = self.G_level[level](batch)
        return res #[b-1,2,256,256]

    def forward(self, xs): #[5,8,256,256]
        Rs = Variable(torch.zeros(self.r_shape).cuda())
        rs = Variable(torch.zeros(self.r_shape).cuda())
        ys = Variable(torch.zeros(self.shape).cuda())

        for i in range(self.levels):
            j = self.levels-i-1
            if j<self.levels-1:
                R = 2*self.up_crop(Rs[j+1])
            else:
                R = torch.zeros_like(Rs[j])
            x = self.render(xs[j,:,:,:], R) #[8,256,256]
            rs[j] = self.compute_res(x, j) #[7,256,256,2]
            Rs[j] = rs[j] + R

            ys[j] = self.render(xs[j], Rs[j])[1:] #[7,256,256]

        return ys, Rs, rs #[5,b-1,256,256], [5, b-1,2,256,256], [b-1,2,256,256]

#Simple test
if __name__ == "__main__":
    flow = Xmas().cuda()
    s = np.ones((5,8,256,256), dtype=np.float32)
    x = torch.from_numpy(s).cuda()
    xs = torch.autograd.Variable(x, requires_grad=False)
    flow = flow(xs)
    print(len(flow)) #expected output (7,256,256,2)
