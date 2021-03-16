"""
Module that implements the phase retrieval algorithm used by the FROG software
*********
The realization of this GP (general projections) phase retrieval algorithm
in here is based on the Matlab code from Steven Byrnes who wrote an extension
of Adam Wyatt's MATLAB FROG program. Various features include anti-aliasing
algorithm.

Copyright (c) 2012, Steven Byrnes
Copyright (c) 2009, Adam Wyatt
All rights reserved.

Disclaimer:
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*********

Julian Krauth tranlated the code from Matlab into Python for the use in the
shg_frog python package.

File name: phase_retrieval.py
Author: Julian Krauth
Date created: 2019/11/27
Python Version: 3.7
"""
import pathlib
import numpy as np
import imageio
import yaml

from pyqtgraph.Qt import QtGui, QtCore
import pyqtgraph as pg


def rms_diff(F1: np.ndarray, F2: np.ndarray) -> float:
    """ Calculates RMS difference in the entries of two real matrices/vectors. """
    result = np.sqrt(np.mean(np.square(F1-F2)))
    return result

def normalize_max_one(arr: np.ndarray) -> np.ndarray:
    """ Normalize a matrix or vector for its maximum to be 1.
    Must have real nonnegative entries.
    """
    result = arr/np.amax(arr)
    return result

def calc_alpha(Fm, Fr):
    """ Calculates alpha, the positive number that minimizes
    rms_diff(Fm,alpha*Fr). See DeLong1996
    """
    result = np.sum(Fm*Fr)/np.sum(np.square(Fr))
    return result

def parity(x):
    """
    1 for odd, 0 for even. Don't delete! It looks like it's not used
    in the program, but it can be called by the 'method' strings.
    """
    result = int(x-2*np.floor(x/2.))
    return result

def make_axis(length: int, step: float) -> np.ndarray:
    """ Create an array that contains the values of a time or frequency axis,
    given the step size and the length of the array. The values are centered
    around zero.

    Arguments:
        length -- length of array
        step -- step size

    Returns:
        axis -- horizontal array for use as a plot axis
    """
    axis = np.arange(-length/2 * step, length/2*step, step)
    return axis


def shift_signal(sig_in: np.ndarray, shift: float, freq_axis: np.ndarray):
    """
    Shift pulse in time by a given delay.
    Arguments:
    sig_in -- complex float dim 128, pulse field (in time)
    d -- float, a delay picked from the delay vector
    F -- float dim 128, vector of frequencies

    Returns:
    sig_out -- complex float dim 128, pulse field
    """
    sig_freq_domain = np.fft.fft(sig_in, axis=0)
    sig_out = np.fft.ifft(sig_freq_domain * np.exp(1.j*2*np.pi*shift*freq_axis), axis=0)
    return sig_out

class PhaseRetrieval:
    """
    The two main methods of this class are:
    1. prepFROG(): Preparing the measured FROG trace
       to be used for the phase retrieval
    2. retrievePhase(): Uses the prepared FROG trace and retrieves
       the pulse shape in time and frequency domain.

    The retrievePhase() method uses two additional methods for
    the algorithm:
    - makeFROG()
    - guessPusle()
    """
    def __init__(
        self,
        max_iter: int=200,
        prep_size: int=128,
        GTol: float=0.001,
        folder: str='out_test/',
        fm_name: str='frog_meas'):

        # Names and type of FROG trace files
        self.ftype = '.tiff' # Use tiff, because of 16bit capabilities
        self.fm_name = folder + fm_name # Original measured trace
        self.fp_name = folder + 'frog_prep' # Preparated trace
        self.fr_name = folder + 'frog_reco' # Reconstructed trace


        ### Parameters used by prepFROG ###

        # Pixel number of preparated FROG trace, which will be used by
        # phase retrieval algorithm, sometimes later also called N
        self.prep_size = prep_size

        # Difference in delay between consecutive pixels
        # of the starting image in ps
        self.ccddt = 0.002

        # Difference in frequency between consecutive pixels
        # of the starting image in THz
        self.ccddv = 0.2


        ### Parameters used by phaseRetrieve ###

        # Difference in time-delay (dt) between consecutive pixels. This
        # automatically fixes frequency units, too. Doesn't affect algorithm,
        # only plots and such. This value will be updated by prepFROG
        self.dtperpx = 0.0308
        # Frequency interval per pixel, given by self.dtperpx
        #self.dvperpx = 1 / (self.N*self.dtperpx)

        # Cell-array: units[0] is units of dtperpx, units[1] is units[0]^-1,
        # the units of frequency.
        self.units = ['ps','THz']


        ### FROG traces ###
        # Measured trace
        self.Fccd = None
        # Preparated trace, created by self.setPrepFrogTrace()
        self.Fm = np.zeros((self.prep_size,self.prep_size)).astype(np.float64)
        # Reconstructed trace, created by self.retrievePhase()
        self.Fr = None

        ### Parameters used by retrievePhase method

        # Maximum number of iterations allowed
        self.max_iter = max_iter
        # Initial guess for Pt. Default: None (Choose randomly)
        self.seed = None
        # Tolerance on the error
        self.GTol = GTol
        # 0: No updates while solving. 1: Output movie. 2: Print text.
        self.mov = 1
        # Algorithm choice
        # method[0]: makeFROGdomain      # 0,1,2,3
        # method[1]: makeFROGantialias   # 1 = antialias
        # method[2]: guessPulsedomain    # not used
        # method[3]: guessPulseantialias # not used
        self.method = [0,0,0,0]

        # G=RMS difference in entries of Fm and alpha*Fr
        # (where Fm is normalized so max(Fm)=1 and alpha is whatever
        # value minimizes G.) See DeLong1996

    def set_size(self, val: int):
        """ Sets the size that is used by prepFROG to prepare the
        FROG trace.
        """
        self.prep_size = val

    def set_max_iterations(self, val: int):
        """ Sets the maximum iterations of the phase retrieval. """
        self.max_iter = val

    def set_tolerance(self, val: float):
        """ Sets the tolerance on the error between reconstructed and
        original FROG trace. If the error is smaller than the tolerance
        the retrieval ist stopped. """
        self.GTol = val

    def setFccd(self, val):
        self.Fccd = val

    def setFccdSig(self, dummy, val):
        if dummy==2:
            self.setFccd(val)



    def load_seed(self, path: pathlib.Path):
        """
        Load a custom seed for the retrieval from a file
        and put it into the seed attribute.
        """
        # file was originally loaded from 'seed/seed.input'
        seed_real, seed_imag = np.loadtxt(path, unpack=True)
        seed = seed_real + 1j * seed_imag
        self.seed = seed.reshape(self.prep_size, 1)



    def prepFROG(
        self,
        ccddt: float=None,
        ccddv: float=None,
        ccdimg: np.ndarray=None,
        showprogress: int=0,
        showautocor: int=0,
        flip: int=2):
        """
        prepFROG: Cleans, smooths, and downsamples data in preparation for
        running the FROG algorithm on it.
        The following attributes have to be set before using this method:
        self.ccddt
        self.ccddv
        self.prep_size
        """
        print('Prepare FROG trace...')

        #if ccddt is None: ccddt = self.ccddt
        #if ccddv is None: ccddv = self.ccddv
        if ccdimg is None:
            if False:
            # if self.Fccd is not None:
                print("Read data from memory")
                ccdimg = self.Fccd
                ccddt = self.ccddt
                ccddv = self.ccddv
            else:
                print(f"Read data from file {self.fm_name}{self.ftype}")
                ccdimg = imageio.imread(self.fm_name+self.ftype)
                with open(self.fm_name+'.yml', 'r') as f:
                    properties = yaml.load(f)
                #ccdd = np.loadtxt(self.fm_name+'.txt')
                ccddt = properties['ccddt']
                ccddv = properties['ccddv']
                if ccdimg.ndim==3:
                    ccdimg = ccdimg[:,:,0]
                ccdimg = np.asarray(ccdimg,dtype=np.float64)
                # Set also value of attribute
                #self.Fccd = ccdimg
                #self.ccddt = ccddt
                #self.ccddv = ccddv

        # ccddtdv = ccddt * ccddv, with units of "cycles per horizontal-pixel
        # per vertical-pixel". This product ccddtdv is an important parameter
        # for the FROG algorithm, but ccddt and ccddv are NOT themselves
        # important. They are only used for graph labels.
        ccddtdv = ccddt * ccddv

        # Choose correct image orientation
        if flip in (1, 3):
            ccdimg = np.transpose(ccdimg)
        if flip in (2, 3):
            ccdimg = np.flipud(ccdimg)

        # Find the approximate center of the spot, by calculating an average
        # coordinate weighted by row-sums or column-sums.
        ccdsizev = np.size(ccdimg,0) #ccdsizev is how many rows
        ccdsizet = np.size(ccdimg,1) #ccdsizet is how many cols
        colsums = np.sum(ccdimg,0)
        centercol = np.inner(np.arange(1,ccdsizet+1),colsums) / np.sum(colsums)
        rowsums = np.sum(ccdimg,1)
        centerrow = np.inner(np.arange(1,ccdsizev+1),rowsums) / np.sum(rowsums)


        # Find the (very) approximate width of the spot in each dimension
        spotwidth = (2*np.inner(np.abs(np.arange(1,ccdsizet+1)-centercol),colsums)
                     / np.sum(colsums))
        spotheight = (2*np.inner(np.abs(np.arange(1,ccdsizev+1)-centerrow),rowsums)
                      / np.sum(rowsums))


        # Large "aspectratio" means vertical-stripe original image. Can also
        # input this or modify it by hand depending on what works best. This
        # is relevant because the final image will scale the dimensions to
        # make the final image aspect ratio roughly 1. (This helps accuracy).
        aspectratio=spotheight/spotwidth


        # vpxpersample and tpxpersample are the separation between consecutive
        # "samples" to be fed into the FROG algorithm. There are N*N=N^2
        # "samples" total, each is a pixel taken from the CCD image. They
        # satisfy these two equations:
        # (A): vpxpersample / tpxpersample = aspectratio (this helps accuracy)
        # (B): (vpxpersample * ccddv) * (tpxpersample * ccddt) = 1/N (this is
        # FFT requirement)
        vpxpersample = np.sqrt((1/(self.prep_size*ccddtdv))*aspectratio)
        tpxpersample = np.sqrt((1/(self.prep_size*ccddtdv))/aspectratio)

        #if showprogress:
        print('Vertical pixels per freq (v) sample: %.3f' % vpxpersample)
        print('Horizontal pixels per delay (t) sample: %.3f' % tpxpersample)

        # For me these are around 5 pixels.


        ################# IMAGE FILTERING #################
        if showprogress:
            plt.figure('prepFROG',figsize=(7,6))
            plt.subplot(221)
            plt.imshow(ccdimg)
            plt.title('(1) Original')

        #### LOW-PASS FOURIER FILTERING ####
        # See Taft and DeLong, chapter 10 in FROG textbook
        rho=0.3 # Lower rho means more extreme filtering. Make sure image looks OK.
        maxtimesrho = max([ccdsizev, ccdsizet])*rho
        ccdimgfft=np.fft.fftshift(np.fft.fft2(ccdimg))
        tophatfilter=np.zeros((ccdsizev, ccdsizet))
        for ii in range(ccdsizev):
            for jj in range(ccdsizet):
                if(np.sqrt(
                        np.square((ii+1)-ccdsizev/2.)+np.square((jj+1)-ccdsizet/2.))
                   <maxtimesrho):
                    tophatfilter[ii,jj]=1

        ccdimgfft = tophatfilter * ccdimgfft
        ccdimg = abs(np.fft.ifft2(np.fft.ifftshift(ccdimgfft)))
        if showprogress:
            plt.subplot(222)
            plt.imshow(ccdimg)
            plt.title('(2) After Fourier filter')

        #### BACKGROUND SUBTRACTION ####
        # The lowest-average-intensity 8x8 block of pixels is assumed to be the
        # background and is subtracted off
        imgblocks = np.zeros((ccdsizev, ccdsizet))
        for ii in range(8):
            ccdimg_rollv = np.roll(ccdimg, ii+1, axis=0)
            for jj in range(8):
                ccdimg_rollvt = np.roll(ccdimg_rollv, jj+1, axis=1)
                imgblocks = imgblocks + ccdimg_rollvt

        background = np.amin(imgblocks)/(8.*8.)

        ccdimg = ccdimg - background
        ccdimg[ccdimg<0] = 0 # Negative values are set to zero
        if showprogress:
            plt.subplot(223)
            plt.imshow(ccdimg)
            plt.title('(3) After background subtraction')

        #### DOWNSAMPLING TO NxN ####
        # Want an NxN pixel image to process. Go through each pixel of the
        # original, and have it contribute to the nearest pixel of the final
        # (in an average).
        if(np.shape(ccdimg)==(self.prep_size, self.prep_size) and ccddt * ccddv == 1./float(self.prep_size)):
            # Skip downsampling if ccdimg is already sampled correctly.
            fnlimg = ccdimg
            fnldt = ccddt
        else:
            fnlimg = np.zeros((self.prep_size, self.prep_size))
            # How many times you've added onto that pixel
            fnlimgcount = np.zeros((self.prep_size, self.prep_size))
            for ii in range(ccdsizev):  # Which row? (which freq?)
                rowinfinal = int(round(self.prep_size/2.+((ii+1)-centerrow)/vpxpersample))-1
                if(rowinfinal<0 or rowinfinal>=self.prep_size):
                    continue
                for jj in range(ccdsizet):
                    colinfinal = int(round(self.prep_size/2.+((jj+1)-centercol)/tpxpersample))-1
                    if(colinfinal<0 or colinfinal>self.prep_size-1):
                        continue
                    fnlimgcount[rowinfinal, colinfinal] += 1
                    fnlimg[rowinfinal, colinfinal] += ccdimg[ii,jj]
            fnlimgcount[fnlimgcount==0] = 1 # Avoid dividing by zero.
                                            # Pixels that haven't been written
                                            # into should be set to zero, and
                                            # they are.

            fnlimg = fnlimg / fnlimgcount
            fnldt = ccddt * tpxpersample
            #print(f'fnldt: {fnldt}  ccddt: {ccddt} tpxpersample: {tpxpersample}')


        #### Save results in corresponding attributes ####
        self.dtperpx = fnldt # set freq interval per pixel.
        self.Fm = fnlimg # set prep. Frog image for phase retrieval.

        if showprogress:
            plt.subplot(224)
            plt.imshow(fnlimg)
            plt.title('(4) After downsampling to %dx%d' % (self.prep_size, self.prep_size))
            plt.subplots_adjust(
                left=0.06, bottom=0.06, right=0.94, top=0.94, wspace=0.1, hspace=0.3
                )

        if showautocor:
            plt.figure('Autocorrelation',figsize=(6, 4))
            plt.plot(
                np.arange(-ccdsizet/2.*ccddt, ccdsizet/2.*ccddt, ccddt),
                np.sum(ccdimg, 0)
                )
            plt.title('Autocorrelation')
            plt.xlabel('Delay')
            plt.ylim((0, 1.05*max(np.sum(ccdimg, 0))))

        if showprogress or showautocor:
            plt.show()

        if 0: # Save prepFROG image
            fnlimg = np.rint(fnlimg * 65535 / np.amax(fnlimg))
            #fnlimg = fnlimg * 255 / np.amax(fnlimg)
            #print np.amax(fnlimg)
            fnlimg = np.asarray(fnlimg, dtype=np.uint16)
            imageio.imsave('prep_frog.tiff', fnlimg)






    def makeFROG(self, Pt: np.ndarray, domain: int=0, antialias: int=0):
        """
        makeFROG: Reads in the (complex) electric field as a function of time,
        and computes the expected SHG-FROG trace.

        Arguments:
        Pt -- vertical array, complex electric field
        domain -- 0: delay space, 1: frequency space
        antialias --

        Return:
        [F, EF] -- frog intensity trace, frog complex field trace
        """

        N = len(Pt) # or: N = self.N

        if domain==0:
            EF = np.outer(Pt, Pt)

            if antialias:
    	        # Anti-alias: Delete entries that come from spurious
    	        # wrapping-around. For example, an entry like P2*G(N-1) is spurious
    	        # because we did not measure such a large delay. For even N, there
    	        # are terms like P_i*G_(i+N/2) and P_(i+N/2)*G_i, which correspond
    	        # to a delay of +N/2 or -N/2. I'm deleting these terms. They are
    	        # sort of out-of-range, sort of in-range, because the maximal delay
    	        # in the FFT can be considered to have either positive or negative
    	        # frequency. This is the outer edge of the FROG trace so it
    	        # should be zero anyway. Deleting both halves keeps everything
    	        # symmetric when sign of delay is flipped.
                EF = EF - np.tril(EF,-np.ceil(N/2.)) - np.triu(EF,np.ceil(N/2.))

            for n in range(0, N):
                # Row rotation...Eqs.(10)-->(11) of Kane1999
                EF[n,:] = np.roll(EF[n,:], -n)

            # EF is eqn (11) of Kane1999. From left column to right column, it's
            # tau=0,-1,-2...3,2,1

            # Permute the columns to the right order, tau=...,-1,0,1,...
            EF = np.fliplr(np.fft.fftshift(EF, 1))

	        # FFT each column and put 0 frequency in the correct place:
            EF = np.roll(np.fft.fft(EF, None, axis=0), int(np.ceil(N/2.)-1), 0)

            # Generate FROG trace (= |field|^2)
            F = np.square(np.absolute(EF))

            return F, EF


        if domain==1:
  	        # Frequency-domain integration; in other words,
	        # integral[P(w') * P(w-w')e^(-iw'tau)dw']. We follow Kane1999,
            # but starting in frequency-frequency space rather than
            # delay-delay space.
            PtFFT = np.fft.fft(Pt, axis=0)
            #EF = PtFFT*np.transpose(PtFFT)
            EF = np.outer(PtFFT, PtFFT)

  	        # Right now the (i,j) (i'th row and j'th column) entry of EF
            # corresponds to the product of the i'th Fourier component
            # with the j'th.
	        # But keep in mind "1st Fourier component" is v=0, the 2nd is v=1,
	        # etc., in the order 0,1,2,...,-2,-1.

            if antialias:
   	            # Anti-alias. The product P(v1)*P(v2) contributes to the signal at
	            # frequency v1+v2. This can only be relevant if v1+v2 is in the
	            # range of the FROG trace. Otherwise the term is deleted. When N is
	            # even, there is a "maximal frequency", which can correspond
	            # equally well to +N/2 or -N/2 frequency. We assume it's positive.
	            # That's consistent with the convention above for the FROG trace,
	            # that places zero frequency in such a way that more positive
	            # frequencies are visible than negative frequencies for even N.
                vmax = int(np.floor(N/2.)) # Which index in EF uses
                                           # the max positive freq?
                vmin = vmax+1 # Which index in EF uses the most negative freq?
                for n in range(1,vmax+1):
                    EF[n,vmax-(n-1):vmax+1] = 0
                for n in range(vmin,N):
                    EF[n,vmin:vmin+(N-n)] = 0

            EF = np.fliplr(EF)

            # Row rotation...analogous to Eqns (10)-->(11) of Kane1999
            for n in range(0, N):
                EF[n,:] = np.roll(EF[n,:], -n)

 	        # FT each column
            EF = np.fft.ifft(EF, None, axis=0)
	        # Right now the columns are in the order N*v=(N-1,N-2,...,1,0), and rows
	        # are in the order tau=0,-1,...,1. (These are all mod N.)
            EF = np.flipud(np.fliplr(EF))
	        # Now columns are N*v=(0,1,2,...-1) and rows are tau=1,2,3,...,0.
            EF = np.roll(EF, int(np.ceil(N/2.)), 0)
            EF = np.roll(EF, int(np.ceil(N/2.)-1), 1)

	        # Now zero frequency & zero delay is at (ceil(N/2.),ceil(N/2.))
            # as desired
            EF = np.transpose(EF)

	        # Generate FROG trace (= |field|^2)
            F = np.square(np.absolute(EF))

            return F, EF



    def guessPulse(
        self,
        EF: np.ndarray,
        lastPt: np.ndarray,
        domain: int=0,
        antialias: int=0,
        PowerOrSVD: int=0):
        """
        guesspulse: Extracts the pulse as a function of time, starting with a FROG
        FIELD (i.e. complex amplitude), and the previous best-guess pulse. Uses
        either "power method" from Kane1999 or SVD method from Kane1998.
        """

        N = len(EF[0])


        if domain==0:
            # Do the exact inverse of the procedure in makeFROG...
            # Undo the line:
            # EF = np.roll(np.fft.fft(EF,None,axis=0),int(np.ceil(N/2.)-1),0)
            EF = np.fft.ifft(np.roll(EF, -int(np.ceil(N/2.)-1),0), None, axis=0)

            # Undo the line: EF = np.fliplr(np.fft.fftshift(EF,1))
            EF = np.fft.ifftshift(np.fliplr(EF),1)

            # Undo the lines:
            #      for n in range(0,N):
            #          EF[n,:] = np.roll(EF[n,:],-n)
            for n in range(0,N):
                EF[n,:] = np.roll(EF[n,:],n)
            # Now EF is the "outer product form", see Kane1999

            if antialias:
                # Anti-alias in time domain. See makeFROG for explanation.
                EF = EF - np.tril(EF,-np.ceil(N/2.)) - np.triu(EF,np.ceil(N/2.))

            if PowerOrSVD==0: # Power method
                #lastPt = np.transpose(lastPt) # Make column array
                Pt = np.dot(EF,np.dot(np.conjugate(np.transpose(EF)),lastPt))
                #Pt = np.transpose(Pt) # Change to row array again
            else:             # SVD method (does not work???)
                U, S, V = np.linalg.svd(EF)
                Pt = U[:,0]
                Pt = Pt.reshape(N,1)

            # Normalize to Euclidean norm 1
            Pt = Pt / np.linalg.norm(Pt)
            return Pt


        if domain==1:
	        # Do the exact inverse of the procedure in makeFROG...
	        # Undo the line: EF = np.transpose(EF)
            EF = np.transpose(EF)

            # Undo the lines:
            # EF = np.roll(EF,int(np.ceil(N/2.)),0)
            # EF = np.roll(EF,int(np.ceil(N/2.)-1),1)
            EF = np.roll(EF,-int(np.ceil(N/2.)-1),1)
            EF = np.roll(EF,-int(np.ceil(N/2.)),0)
            # Undo the line: EF = np.flipud(np.fliplr(EF))
            EF = np.flipud(np.fliplr(EF))
            # Undo the line: EF = np.fft.ifft(EF,None,axis=0)
            EF = np.fft.fft(EF,None,axis=0)
            # Undo the lines:
            # for n in range(0,N):
            #     EF[n,:] = np.roll(EF[n,:],-n)
            for n in range(0,N):
                EF[n,:] = np.roll(EF[n,:],n)
	        # Undo the line: EF = np.fliplr(EF)
            EF = np.fliplr(EF)
            # Now we're up to the lines in makeFROG:
            # PtFFT=np.fft.fft(Pt,axis=0); EF = np.outer(PtFFT,PtFFT)

            if antialias:
	            # Anti-alias in frequency domain. See makeFROG for explanation.
                vmax = np.floor(N/2.)
                vmin = vmax+1
                for n in range(2,vmax+1):
                    EF[n,vmax-(n-1):vmax+1] = 0
                for n in range(vmin,N):
                    EF[n,vmin:vmin+(N-n)] = 0


            if PowerOrSVD==0: # Power method
                lastPtFFT = np.fft.fft(lastPt,axis=0)
                Pt = np.fft.ifft(np.dot(EF,np.dot(np.conjugate(np.transpose(EF)),
                                                  lastPtFFT)),axis=0)
            else: # SVD method
                U, S, V = np.linalg.svd(EF)
                Pt = np.fft.ifft(U[:,0])
                Pt = Pt.reshape(N,1)

            Pt = Pt / np.linalg.norm(Pt)  # Normalize to Euclidean norm 1

            return Pt




    def retrievePhase(
        self, Fm: np.ndarray=None, mov: int=0, dtperpx: float=None, # Will be set by prepFROG
        units=None, signal_data=None, signal_label=None, signal_title=None,
        signal_axis=None):
        """
        Retrieves the phase using the prepared FROG trace Fm.
        This method makes use of makeFROG and prepFROG.
        Arguments:
        Fm -- prepared frog trace from prepFROG
        mov -- 1: show plot while running
        dtperpx -- time step between pixels
        units --
        signal_data --
        signal_label --
        signal_title --
        signal_axis --
        """

        print('Retrieve pulse...')

        if Fm is None:
            if int(np.sum(self.Fm)) != 0:
                Fm = self.Fm
            else:
                print(f"Read data from file {self.fp_name}{self.ftype}")
                Fm = imageio.imread(self.fp_name+self.ftype)
                if Fm.ndim==3:
                    Fm = Fm[:,:,0]
                Fm = np.asarray(Fm,dtype=np.float64)

        N = len(Fm[0])

        # Time interval per pixel
        if dtperpx is None: dtperpx = self.dtperpx

        # Frequency interval per pixel
        dvperpx = 1 / (N*dtperpx)

        if units is None:
            dtunit = self.units[0]
            dvunit = self.units[1]
        print(f'prepFROG: dt: {dtperpx}{dtunit} dv: {dvperpx}{dvunit}')

        # x-axis labels for plots
        tpxls = make_axis(N, dtperpx)
        # y-axis labels for plots
        vpxls = make_axis(N, dvperpx)

        # Emit axis scale information
        if signal_axis is not None:
            signal_axis.emit(tpxls, vpxls)

        # Maybe you only want to display part of the plot range, to zoom in
        # on the interesting stuff. If so, edit the following lines...
        tplotrange = [np.min(tpxls), np.max(tpxls)]
        vplotrange = [np.min(vpxls), np.max(vpxls)]


        # Make a randam guess for a seed if no external seed has been loaded.
        if self.seed is None:
            # Generate initial guess of gate and pulse from noise times
            # a gaussian envelope function. Don't use complex phase 0 or
            # it gets stuck in real numbers, but don't let the complex
            # phase vary too much or it has aliasing problems.
            Pt = (np.exp(
                -2. * np.log(2.) * np.square( (np.arange(0, N) - N/2.) / (N/10.) )
                ) * np.exp(0.1*2.*np.pi*1.j*np.random.rand(1, N)))
            # Used as a vertical array
            Pt = Pt.reshape(N, 1)
        else:
            Pt = self.seed

        # Normalize FROG trace to unity max intensity
        Fm = normalize_max_one(Fm)

        ###################
        # Start main part #
        ###################

        # Generate FROG trace
        iteration = 0

        makeFROGdomain = self.method[0]
        makeFROGantialias = self.method[1]
        #guesspulsedomain = self.method[2] # not used
        #guesspulseantialias = self.method[3] # not used

        # EFr is reconstructed FROG trace complex amplitudes ( Fr=|EFr|^2 )
        Fr, EFr = self.makeFROG(Pt, makeFROGdomain, makeFROGantialias)

        # Calculate FROG error G, see DeLong1996
        Fr = Fr * calc_alpha(Fm, Fr) #scale Fr to best match Fm, see DeLong1996
        G = rms_diff(Fm, Fr)

        if mov==0 and signal_data is not None and signal_label is not None:
            signal_data.emit(0, Fm)
            signal_label.emit(self.units)
        elif mov==1:
            # Interpret image data as row-major instead of col-major
            pg.setConfigOptions(imageAxisOrder='row-major')
            pg.mkQApp()
            win = pg.GraphicsWindow()
            #win.resize(800,500)
            win.setWindowTitle('Phase Retrieval - SHG FROG')
            #win.show()

            p1 = win.addPlot(title='Orig. FROG trace')
            img1 = pg.ImageItem()
            p1.addItem(img1)
            img1.setImage(Fm)
            p1.setLabel('bottom','Delay [%s]' % dtunit)
            p1.setLabel('left','SH freq [%s]' % dvunit)

            p2 = win.addPlot()
            img2 = pg.ImageItem()
            p2.addItem(img2)
            p2.setLabel('bottom','Delay [%s]' % dtunit)
            p2.setLabel('left','SH freq [%s]' % dvunit)

            win.nextRow()
            p3 = win.addPlot(colspan=2)
            p3.setLabel('bottom','Time [%s]' % dtunit)
            p3.setLabel('left','|E|^2 & ang(E)')
            p3p = p3.plot(tpxls,np.zeros(N),pen=(255,0,0))
            p3p2= p3.plot(tpxls,np.zeros(N),pen=(0,255,0))

            win.nextRow()
            p4 = win.addPlot(colspan=2)
            p4.setLabel('bottom','Frequency [%s]' % dvunit)
            p4.setLabel('left','|E|^2 & ang(E)')
            p4p = p4.plot(vpxls,np.zeros(N),pen=(255,0,0))
            p4p2= p4.plot(vpxls,np.zeros(N),pen=(0,255,0))


        #  --------------------------------------------------
        #  F R O G   I T E R A T I O N   A L G O R I T H M
        #  --------------------------------------------------

        while G>self.GTol and iteration<self.max_iter:
            # Keep count of no. of iterations
            iteration += 1
            if mov==2:
                print(f"Iteration number: {iteration} Error: {G:.04f}")

            # Check method to use. Have to run this inside the loop because method
            # may vary depending on iter.
            makeFROGdomain = self.method[0]
            makeFROGantialias = self.method[1]
            guesspulsedomain = self.method[2]
            guesspulseantialias = self.method[3]

            # Update best-guess EFr: Phase from last makeFROG, amplitudes from Fm.
            # Change absolute values of EFr to match Fm (keep phase the same)
            # and avoid dividing by zero.
            EFr = EFr * np.sqrt(
                np.divide(Fm, Fr, out=np.zeros_like(Fm), where=Fr!=0)
                )
            # Extract pulse field from FROG complex amplitude
            #testPt = Pt

            Pt = self.guessPulse(EFr, Pt, guesspulsedomain, guesspulseantialias)

            ### Keep peak centered... not necessary, but this helps when visually
            ### comparing and understanding reconstructions.
            if True:
                # Weighted average to find center of peak
                centerindex = (
                    np.sum(
                        np.arange(1, N+1).reshape(N, 1) * np.absolute(np.power(Pt, 4))
                        ) / np.sum(np.absolute(np.power(Pt, 4))))
                Pt = np.roll(Pt,-int(np.round(centerindex-N/2.)))

            # Make a FROG trace from new fields
            Fr, EFr = self.makeFROG(Pt, makeFROGdomain, makeFROGantialias)

            # Calculate FROG error G, see DeLong1996
            # Scale Fr to best match Fm, see DeLong1996
            Fr = Fr * calc_alpha(Fm, Fr)
            G = rms_diff(Fm, Fr)

            print(f"Iter. {iteration:3}: FROG Error {G:.4f}")


            # Create plotting data
            # time domain
            tPt_data = 2*np.pi*np.square( np.absolute(Pt[:, 0]) ) \
                / np.square( np.amax(np.absolute(Pt)) )

            tPt_angle = np.angle(Pt[:,0])+np.pi
            # frequency domain
            FFTPt = np.fft.fftshift(np.fft.fft(np.fft.fftshift(Pt), axis=0))
            vPt_data = 2*np.pi*np.square(np.absolute(FFTPt[:,0]))\
                / np.square(np.amax(np.absolute(FFTPt)))
            vPt_angle = np.angle(FFTPt[:,0])+np.pi
            if mov==0 and signal_data is not None and signal_title is not None:
                signal_data.emit(1, Fr)
                signal_data.emit(2, tPt_data)
                signal_data.emit(3, tPt_angle)
                signal_data.emit(4, vPt_data)
                signal_data.emit(5, vPt_angle)
                signal_title.emit(iteration, G)
            elif mov==1:
                p2.setTitle(title='Reconstructed: iter=%d Err=%.4f' % (iteration, G))
                img2.setImage(Fr)
                p3p.setData(tpxls, tPt_data)
                p3p2.setData(tpxls, tPt_angle)
                p4p.setData(vpxls, vPt_data)
                p4p2.setData(vpxls, vPt_angle)

                pg.QtGui.QApplication.processEvents()
                #time.sleep(.1)

        #    self.screenshot()
        #  ------------------------------------------------------------
        #  E N D   O F   A L G O R I T H M
        #  ------------------------------------------------------------

        # Save the complex electric field for test purpose
        #np.savetxt('seed/seed_new.input', np.column_stack([Pt.real, Pt.imag]))


        self.Fr = Fr
        print('Phase retrieval finished!')


        if __name__ == '__main__':
            import sys
            if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
                QtGui.QApplication.instance().exec_()


    def ePIE_fun_FROG(
        self, I: np.ndarray=None, dt: np.ndarray=None, df: np.ndarray=None,
        signal_data=None, signal_label=None, signal_title=None,
        signal_axis=None):
        """
        Function the reconstructs a pulse function (in time) from a
        SHG FROG trace by use of the Ptychographic algorithm.
        No prior knowledge needed.

        Arguments:
        I       =   float dim 128x128, Experimental / Simulated SHG FROG Trace
        dt       =   float dim 128, vector of delays that coresponds to trace.
        df       =   float dim 128, vector of frequencies.

        Returns:
        Obj     =   complex float dim 128, Reconstructed pulse field (in time).
        Ir      =   float dim 128x128, reconstructed FROG trace.
        error   =   float dim 200, vector of errors for each iteration
        """

        if I is None and dt is None and df is None:
            # Use trace prepared by prepFROG
            I = self.Fm * 65535 / np.amax(self.Fm)
            I = np.rint(I).astype('float64')
            dt = self.dtperpx
            df = 1/(I.shape[1]*dt)

        #print(I.dtype, I.shape, np.max(I), np.min(I))
        #sys.exit()

        # (Frequencies, Delays) = shape
        (N, K) = I.shape
        # Make a time axis
        D = make_axis(K, dt)
        # We need a vertical frequency axis here
        F = make_axis(N, df).reshape(N, 1)
        # Create bool array that yields which frequencies from the frequency axis
        # are good to use. One could for example only measure every second frequency.
        # then we would use the modulo with 2 here.
        # Originally Fsupp was an argument of this function.
        Fsupp = np.array([True if i%1 == 0 else False for i in range(N)])

        # Check input:
        n_freq = N
        n_delay = K
        assert I.shape == (n_freq, n_delay)
        assert I.dtype == np.dtype('float64')
        assert D.shape == (n_delay,)
        assert D.dtype == np.dtype('float64')
        assert Fsupp.shape == (n_freq,)
        assert Fsupp.dtype == np.dtype('bool')
        assert F.shape == (n_freq, 1)
        assert F.dtype == np.dtype('float64')

        # Sum over frequency axis yields the initial guess for the algorithm.
        # This corresponds to the intensity autocorrelation of the pulse.
        #Obj = np.sum(I, axis=0) \
        #    / np.sqrt(np.sum(np.abs( np.sum(I, axis=0) )**2))
        #Obj = Obj.reshape(K, 1)
        # Use Gaussian
        # Obj = (np.exp(
        #     -2. * np.log(2.) * np.square( (np.arange(0, N) - N/2.) / (N/10.) )
        #     ) * np.exp(0.1*2.*np.pi*1.j*np.random.rand(1, N)))
        # Obj = Obj.reshape(K,1)
        # Load seed
        Obj = np.loadtxt('seed_ptych.input').view(complex).reshape(-1).reshape(N, 1)

        # del1 = 1e-3
        # del2 = 2e-6
        error = 1
        # This will be the reconstructed trace
        Ir = np.zeros(I.shape)

        # Send axis to plot
        if signal_axis is not None:
            signal_axis.emit(D, np.concatenate(F))

        if signal_data is not None and signal_label is not None:
            signal_data.emit(0, I)
            signal_label.emit(self.units)

        i = 1
        while error > self.GTol and i <= self.max_iter:
            # Produce random array of integers from 0 to K-1
            s = np.random.permutation(range(K))

            # Parameter that controls the strength of the update
            # and is selected randomly in each iteration.
            alpha = np.abs( 0.2 + np.random.randn()/20 )

            for iterK in range(K):
                # Calculate the SHG signal of the field
                temp = shift_signal(Obj, D[s[iterK]], F)
                psi = Obj * temp
                # Fourier transform SHG signal
                psi_n = np.fft.fft(psi, axis=0) / N
                phase = np.exp(1.j*np.angle(psi_n))
                amp = np.fft.fftshift( I[:, s[iterK]].reshape(K, 1) )
                psi_n[Fsupp] = amp[Fsupp] * phase[Fsupp]
                # Experimental soft thresholding, uncomment 2 following lines for try
                # psi_n[~Fsupp] = (np.real(psi_n[~Fsupp]) - del2 * np.sign(np.real(psi_n[~Fsupp]))) \
                #         * (np.abs(psi_n[~Fsupp]) >= del2) \
                #     + 1.j*(np.imag(psi_n[~Fsupp]) - del2 * np.sign(np.imag(psi_n[~Fsupp]))) \
                #         * (np.abs(psi_n[~Fsupp]) >= del2)

                # Get the updated SHG signal
                psi_n = np.fft.ifft(psi_n, axis=0)*N

                # Update the pulse with a weight function
                Uo = temp.conjugate() / np.max( (np.abs(temp)**2) )
                Up = Obj.conjugate() / np.max( (np.abs(Obj)**2) )

                Corr1 = alpha * Uo * (psi_n - psi)
                Corr2 = shift_signal(alpha * Up * (psi_n - psi), -D[s[iterK]], F)

                Obj = Obj +  Corr1 + Corr2
                Ir[:, s[iterK]] = np.abs( np.fft.fftshift( np.fft.fft(Obj * temp, axis=0)/N ) )[:,0]


                if iterK % K == 0:
                    error = np.sqrt(np.sum(np.abs( Ir[np.fft.fftshift(Fsupp),:] - I[np.fft.fftshift(Fsupp),:] )**2 )) \
                        / np.sqrt(np.sum(np.abs(I[np.fft.fftshift(Fsupp),:] )**2 ))
                    print(f'Iter:{i:3d}  IterK:{iterK}  alpha={alpha:.4f} Error={error:.4f}')

                    if signal_data is not None and signal_title is not None:
                        time_trace = Obj.reshape(N,)
                        signal_data.emit(1, Ir)
                        signal_data.emit(2, np.abs(2*np.pi*time_trace/np.max(time_trace)))
                        signal_data.emit(3, np.angle(time_trace)+np.pi)
                        signal_title.emit(i, error)

            i += 1
        # Save as seed.
        # np.savetxt('seed_ptych.input', Obj.view(float).reshape(-1, 2))
        return Obj, error, Ir


if __name__ == '__main__':

    import matplotlib.pyplot as plt

    data_path = pathlib.Path(__file__).parents[1] / 'data'

    # make Phase retrieval instance
    pr = PhaseRetrieval()
    trace = np.array(imageio.imread(data_path / 'prep_frog.tiff')).astype('float64')
    with open(data_path / 'prep_meta.yml', 'r') as f:
        meta = yaml.load(f, Loader=yaml.FullLoader)
    dt = meta['ccddt']
    dF = meta['ccddv']

    #pr.prepFROG(ccddt=dt, ccddv=dF, ccdimg=trace)
    field, error, frog_reconstructed = pr.ePIE_fun_FROG(I=trace, dt=dt, df=dF)
    plt.figure('Frog reconstructed')
    plt.imshow(frog_reconstructed)
    plt.figure('amplitude')
    plt.plot(np.abs(field*field.conjugate()))
    plt.plot(np.unwrap(np.angle(field), axis=0))
    plt.show()


    #pg.setConfigOptions(imageAxisOrder='row-major')
    #pg.mkQApp()
    #win = pg.GraphicsWindow()
    #p1 = win.addPlot(title='Orig. FROG trace')
    #img1 = pg.ImageItem()
    #p1.addItem(img1)
    ##img1.setImage(Fm)
    #p1.setLabel('bottom','Delay [%s]' % pr.units[0])
    #p1.setLabel('left','SH freq [%s]' % pr.units[1])
    #
    #p2 = win.addPlot()
    #img2 = pg.ImageItem()
    #p2.addItem(img2)
    #p2.setLabel('bottom','Delay [%s]' % pr.units[0])
    #p2.setLabel('left','SH freq [%s]' % pr.units[1])
    #
    #win.nextRow()
    #p3 = win.addPlot(colspan=2)
    #p3.setLabel('bottom','Time [%s]' % pr.units[0])
    #p3.setLabel('left','|E|^2 & ang(E)')
    ##p3p = p3.plot(tpxls,np.zeros(N),pen=(255,0,0))
    ##p3p2= p3.plot(tpxls,np.zeros(N),pen=(0,255,0))
    #
    #win.nextRow()
    #p4 = win.addPlot(colspan=2)
    #p4.setLabel('bottom','Frequency [%s]' % pr.units[1])
    #p4.setLabel('left','|E|^2 & ang(E)')
    ##p4p = p4.plot(vpxls,np.zeros(N),pen=(255,0,0))
    ##p4p2= p4.plot(vpxls,np.zeros(N),pen=(0,255,0))
    #win.show()

    pr.prepFROG(showprogress=1,showautocor=1)
    pr.retrievePhase(mov=1)

    #im = plt.imshow(pr.Fm,cmap='hot')
    #plt.colorbar(im, orientation='horizontal')
    #plt.show()
