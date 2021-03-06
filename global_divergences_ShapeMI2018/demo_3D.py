#-------------------------------------------------------
#            Code used to generate Fig. 12
#-------------------------------------------------------

import os.path
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + os.path.sep + '..' + os.path.sep + 'common')

import torch
from torch.autograd        import grad
from sparse_distance_bmp   import sparse_distance_bmp
from pykeops.torch         import Kernel

import numpy   as np
import nibabel as nib
from scipy.ndimage.filters import gaussian_filter
from time  import time

use_cuda = torch.cuda.is_available()
tensor   = torch.cuda.FloatTensor if use_cuda else torch.FloatTensor 

s2v = lambda x : tensor([x])

def LoadImage(fname, sampling=None, index = 1) :
    img = nib.load(fname)
    dat, aff = torch.FloatTensor( img.get_data() ), torch.FloatTensor( img.affine )

    # Extract the 1st or 2nd shape (Knee OAI dataset) -------------------
    if   index == 1 :
        dat[dat > 1]  = 0
    elif index == 2 :
        dat[dat <= 1] = 0
        dat = dat - 1
    else : raise NotImplementedError()
    
    if sampling is not None : # Subsample the data ----------------------
        avg = torch.nn.functional.avg_pool3d
        minibatch = dat.unsqueeze(0).unsqueeze(0)
        dat = avg( minibatch, sampling ).data[0,0]
        # Don't forget to rescale the columns of the affine coordinates system:
        for (c, f) in enumerate(sampling) :
            aff[:,c] = f * aff[:,c]

    return dat.cuda(), aff.cuda()


# Load the nifti files
Sampling  = (2,2,1) #(4,4,2) # If your GPU is good enough, feel free to decrease these factors!
shape_ind = 1       # use '2' for the 2nd knee cap
source, aff_source = LoadImage("data/OAI_a.nii.gz", Sampling, index=shape_ind)
target, aff_target = LoadImage("data/OAI_b.nii.gz", Sampling, index=shape_ind)

print("Working with volumes of size :", list(source.shape), ", ", list(target.shape) )

# We will approx. rescale the data to the unit cube, so that our standard parameters make sense
extr  = torch.cuda.FloatTensor( list(source.shape) )
scale = (((aff_source[:3,:3] @ extr)**2).sum() / 3).sqrt() # |(1,1,1)|_2 = sqrt(3)


# Parameters of our data attachment term =======================================================

experiments = {}

experiments["warmup"] = {
    "formula"  : "kernel",
    "k"        : ("energy", None),
}

experiments["energy_distance"] = {
    "formula"  : "kernel",
    "k"        : ("energy", None),
}

for nits in [1, 2, 3, 5] :
    experiments["hausdorff_L1_M_{}its".format(nits)] = {
        "formula" : "hausdorff",
        "p"       : 1,
        "eps"     : .05, # Remember : eps is homogeneous to C(x,y)
        "nits"    : nits,
        "tol"     : 0.,  # Run all iterations, no early stopping!
    }

for p in [1, 2] : # C(x,y) = |x-y|^1 or |x-y|^2
    for eps, eps_s in [ (.01, "S"), (.05, "M"), (.1, "L") ] :
        experiments["sinkhorn_L{}_{}".format(p, eps_s)] = {
            "formula" : "sinkhorn",
            "p"       : p,
            "eps"     : eps**p, # Remember : eps is homogeneous to C(x,y)
            "nits"    : 100,
            "tol"     : 1e-4,
            "assume_convergence" : True,
        }

def test(name, params, verbose=True) :

    # Compute the cost and gradient ============================================================
    t_0 = time()
    cost, grad_src, heatmaps = sparse_distance_bmp(params, source, target, 
                                                           aff_source/scale, aff_target/scale, 
                                                           normalize=True )
    t_1 = time()
    if verbose : print("{} : {:.2f}s, cost = {:.6f}".format( name, t_1-t_0, cost.item()) )


    # Save it (+use Slicer3D to visualize!) ====================================================
    
    grad_src = - grad_src # We want to visualize a descent direction, not the opposite!

    # Nifti standard: 4th dimension is reserved for time; vector coordinates in 5th position
    grad_src = grad_src.unsqueeze(3) # shape (X,Y,Z,3) -> (X,Y,Z,1,3)

    # *crucial* renormalization step:
    # it allows Slicer3D to interpret your gradient as a "v(x)"
    # and not as a "phi(x)" in the equation "phi(x) = x + v(x)".
    grad_src = 30 * grad_src / grad_src.abs().max() # *30, so that we see arrows on the screen...

    # Disturbingly, when displaying transform grids/vector fields,
    # Slicer takes the opposite of the "Z" coordinates...
    grad_src[:,:,:,:,2] = -grad_src[:,:,:,:,2]


    # Unfortunately, when displaying vector fields, Slicer3D seems to use a spline interpolator
    # that oscillates next to sharp transitions. As grad_src goes from 0 to a large vector
    # at the borders of the segmentation mask, we thus have to smooth the grad_src array
    # to prevent the occurence of visualization artifacts.
    grad_src = grad_src.cpu().numpy()
    grad_src = gaussian_filter(grad_src, [5,5,5,0,0], mode='nearest') # spatial blur

    # Finally, we can save our gradient:
    img = nib.Nifti1Image(grad_src, aff_source)
    img.header.set_intent(1007) # You *must* specify the intent flag : "vector"
    img.to_filename('output/3D/' + name +'.nii.gz')

    # Just in case, store the subsampled source and target
    img = nib.Nifti1Image(source.cpu().numpy(), aff_source)
    img.to_filename('output/3D/source.nii.gz')
    img = nib.Nifti1Image(target.cpu().numpy(), aff_target)
    img.to_filename('output/3D/target.nii.gz')

for name, params in experiments.items() :
    test(name, params)

print("Done.")
