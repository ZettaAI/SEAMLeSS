import torch

r'''
Example usage comparing all pairs of adjacent 8x8 slices in a Dx8x8 stack:
    f,p = get_fft_power2(x)  # x is Dx8x8 block as a torch tensor
    rho = get_hp_fcorr(f[:-1,:,:,:], p[:-1,:,:], f[1:,:,:,:], p[1:,:,:])  # 1 slice short 
'''

def get_fft_power2(block):
    f = torch.rfft(block, 2, normalized=True, onesided=True) # currently 2-channel tensor rather than "ComplexFloat"
    # remove redundant components in one-sided DFT (avoid double counting them)
    onesided = f.shape[-2]   # note the last dim is for complex number
    f[..., onesided:, 0, :] = 0
    f[..., onesided:, -1, :] = 0
    p = torch.sum(f*f, dim=-1)
    return f,p

def cut_low_freq(fmask, cutoff_1d = 0, cutoff_2d = 0):
    fmask[..., 0:1+cutoff_1d, :] = 0
    if cutoff_1d>0:
        fmask[..., -cutoff_1d:, :] = 0
    fmask[..., :, 0:1+cutoff_1d] = 0

    fmask[..., 0:1+cutoff_2d, 0:1+cutoff_2d] = 0
    if cutoff_2d>0:
        fmask[..., -cutoff_2d:, 0:1+cutoff_2d] = 0

    return fmask

def masked_corr_coef(a, b, mask, n_thres = 2, fill_value = 2):
    r'''
    Correlation coeff applied on last 2+1(spatial + complex) dimensions, 
    only considering elements specified by the mask.
    `mask` should have one less dimension (no complex number channels).
    Return value keeps all dimensions except the last complex channel dim.
    '''
    floatmask = mask.to(a)[...,None]
    N2 = floatmask.sum(dim=(-3,-2,-1), keepdim=True)*2  # *2: two channels of complex number

    an = a - (floatmask * a).sum(dim=(-3, -2,-1), keepdim=True) / N2
    bn = b - (floatmask * b).sum(dim=(-3, -2,-1), keepdim=True) / N2
    an[~mask] = 0   # an = an*floatmask, if it's faster - actually that might be better (boolean
            #mask indexing doesn't broadcast and I had to require `mask` arg to be 1 dimention short)
    bn[~mask] = 0

    dotproduct = (an * bn).sum(dim=(-3,-2,-1), keepdim=True)
    # future:
    # norm() on pytorch master seems able to take vector value for 'dim' argument now
    rho = dotproduct / (an.flatten(start_dim=-3, end_dim=-1).norm(2, dim=-1) *
                        bn.flatten(start_dim=-3, end_dim=-1).norm(2, dim=-1))[...,None,None,None]

    rho[N2<=2*n_thres] = fill_value
    rho.squeeze_(-1)  # remove dim for complex channel
    
    return rho

def corr_coef(a, b):
    an = a - a.mean()
    bn = b - b.mean()
    rho = an.dot(bn) / (an.norm(2) * bn.norm(2))
    return rho

def get_hp_fcorr(f1, p1, f2, p2):
    r'''
    Correlation coeffecient on high passed and high power frequency components
    Assuming (Dx)8x8 blocks, voxel value in 0-255
    Returns 2 when not enough components satisfy the criteria.
    '''
    blocksize = 8
    #thres = 256/2*blocksize*0.15  # unnormalized FFT  p_element ~ sqrt(N_elements)
    p_thres = 256/2*0.15  # normalized FFT
    n_thres = 3
    mpowersqrd = p1*p2
    
    valid = mpowersqrd > p_thres**4
    
    # ignore low frequency components
    valid = cut_low_freq(valid, cutoff_1d = 0, cutoff_2d = 1)
    
    if 0: # unvectorized version (single pair of slices) as a reference
        N = torch.sum(valid)
        if N > n_thres:
            # this or the cosine similarity built-in in pyTorch?
            rho = corr_coef(f1[valid].flatten(), f2[valid].flatten())
        else:
            rho = 2
    else: # vectorized version: works on stack of many slices
        rho = masked_corr_coef(f1, f2, valid, n_thres = n_thres, fill_value = 2)
        
    return rho
    
