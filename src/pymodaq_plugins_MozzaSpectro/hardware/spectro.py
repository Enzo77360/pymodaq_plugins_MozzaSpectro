# -*- coding: utf-8 -*-
import abc
from enum import Enum
from collections import namedtuple
Acquisition = namedtuple('Acquisition', 'start stop')


class SpectroError(Exception):
    pass

class TriggerTimeoutError(Exception):
    pass

class SpectralUnits(Enum):
    nm = 0
    inv_cm = 1
units_dict = {SpectralUnits.nm: 'nm', SpectralUnits.inv_cm: 'cm^-1'}
quantity_dict = {SpectralUnits.nm: 'Wavelength', SpectralUnits.inv_cm: 'Wavenumber'}


class Spectro(abc.ABC):
    """Interface class for spectrometers"""

    features = {} # device-class specific features
    # such as on_device_average, etc

    def __init__(self):

        self._npixels = None # maximum number of spectral points on device
        self._lambdas = None # array of spectral coordinates [nm], shape: (self._npixels, )
        self._spectrum = None # array of spectral intensities, shape: (acquisition_length, )
        self._connected = False # device connected flag
        self._serial = '' # device identifier
        self.native_units = SpectralUnits.nm # device native spectral units
        # spectrum analyser or FTIR use frequency units

        self.acquisition = Acquisition(None, None) # holds acquisition parameters

    @classmethod
    @abc.abstractmethod
    def get_serials(cls):
        """
        :returns: list of currently connected (recognized by OS) compatible
        devices and theirs identifiers (serials numbers)"""
        pass

    @abc.abstractmethod
    def connect_device(self, serial):
        """connect to a physical device with a given serial number.
        Initialize lambdas and spectrum"""
        self._connected = False

    @abc.abstractmethod
    def disconnect_device(self):
        pass

    @abc.abstractmethod
    def set_exposure(self, exposure):
        """set exposure time in seconds"""
        pass

    @abc.abstractmethod
    def get_exposure(self):
        """returns exposure time in seconds"""
        pass

    @abc.abstractmethod
    def set_ext_trigger(self, flag):
        pass

    @abc.abstractmethod
    def get_ext_trigger(self):
        pass

    @abc.abstractmethod
    def _acquire_spectrum(self, background_mode):
        """set background=True to switch on background acquisition mode"""
        pass

    def make_acquisition(self, start=0, stop=-1, background_mode=False):
        """acquire spectral intensities and store it until requested
        this method blocks the thread and is usually called asynchronously
        start: acquisition range start
        stop: acqisition range end
        background_mode: flag allowing for device-specific background acquisition
        """
        if not self.connected: # set by self.connect_device
            return

        if stop < 0:
            stop += self._npixels
        if stop < 1 or stop >= self._npixels:
            raise ValueError('stop index %d is not in range 1..%d'%(stop, self._npixels))

        if start < 0:
            start += self._npixels
        if start < 0 or start >= stop:
            raise ValueError('start index %d should be >=0 and < stop index %d'%(start, stop))

        self.acquisition = Acquisition(start, stop)
        self._spectrum = self._acquire_spectrum(background_mode)

    @property
    def spectrum(self):
        """return spectral intensities array"""
        return self._spectrum

    @property
    def lambdas(self):
        """returns spectral coordinate array corresponding to spectral intensities"""
        return self._lambdas

    @property
    def connected(self):
        return self._connected

    @property
    def serial(self):
        return self._serial

    def reset(self):
        pass
