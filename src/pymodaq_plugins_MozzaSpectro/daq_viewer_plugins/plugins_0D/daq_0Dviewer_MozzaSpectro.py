import logging
import numpy as np
from threading import RLock
from time import sleep
from pathlib import Path
from pymodaq.utils.daq_utils import ThreadCommand
from pymodaq.utils.data import DataFromPlugins, DataToExport
from pymodaq.control_modules.viewer_utility_classes import DAQ_Viewer_base, comon_parameters, main
from pymodaq.utils.parameter import Parameter
import libmozza.mozza_defines as md
from libmozza.mozza import MozzaUSB, MozzaError
from src.pymodaq_plugins_MozzaSpectro.hardware.spectro import (Spectro, SpectroError,
                                                                                            Acquisition, SpectralUnits,
                                                                                            TriggerTimeoutError)

LOG = logging.getLogger(__name__)


class DAQ_0DViewer_MozzaSpectro(Spectro):
    """Mozza device"""
    calibration_limits = (2000, 12500)

    def __init__(self):
        super(DAQ_0DViewer_MozzaSpectro, self).__init__()

        self.device = MozzaUSB()
        self.resolution_cm = 3.
        self.native_units = SpectralUnits.inv_cm
        self._trigger_delay_us = 0.

        self._acquisition = Acquisition(0, 0)
        self._lock = RLock()
        self.buffer = np.zeros(0, dtype=np.uint8)

        self.correct_amplitude = None  # spectral amplitude correction function. created during device connection
        self.apply_amp_correction = False  # flag for choosing to apply or not amplitude correction
        self.amp_correction = None  # array of multipiers for amplitude correction (depends on spectral axis)

    @classmethod
    def get_serials(cls):
        mozza = MozzaUSB()
        try:
            serials = mozza.get_serials()
        except MozzaError as e:
            raise SpectroError(f"Error in getting Mozza serials {e}")
        return ['Mozza#%d' % num for num in serials]

    def connect_device(self, serial):
        """connect to a physical device with a given serial number.
        Initialize lambdas and spectrum"""
        try:
            serial_num = int(serial.split('#')[1])
        except (IndexError, TypeError):
            raise SpectroError('Bad Mozza device string format: %r' % serial)

        try:
            self.device.connect(serial_num)
        except MozzaError as e:
            raise SpectroError(e)

        # test communication
        try:
            self.device.get_sensors()
        except MozzaError:
            try:
                self.device.reset_all()
            except MozzaError as e:
                raise SpectroError(e)

        min_wn, max_wn = self.calibration_limits
        self._lambdas = 1e7 / np.arange(max_wn, min_wn, -self.resolution_cm)
        self._npixels = len(self.lambdas)
        self._spectrum = np.zeros_like(self._lambdas)
        self._connected = True
        self._serial = serial
        logging.debug('Mozza device connected: %s', self.connected)
        self.device.set_default_params()

        self.load_amp_correction(serial_num)

    def disconnect_device(self):
        self.device.disconnect()

    def set_exposure(self, exposure):
        """set exposure time in seconds"""
        return 0.

    def get_exposure(self):
        """returns exposure time in seconds"""
        return 0.

    def set_ext_trigger(self, flag, apply=False, update_delay=False):
        self.device.acquisition_params.trigger_source = md.EXTERNAL if flag else md.INTERNAL

        if update_delay:
            if self.device.acquisition_params.trigger_source == md.INTERNAL:
                self._trigger_delay_us = self.device.acquisition_params.trigger_delay_us
                self.device.acquisition_params.trigger_delay_us = 0
            else:
                self.device.acquisition_params.trigger_delay_us = int(self._trigger_delay_us)

        if apply:
            with self._lock:
                try:
                    self.device.set_acquisition_params()
                except MozzaError as e:
                    raise SpectroError(f"Error in setting exernal trigger {e}")

    def get_ext_trigger(self):
        return self.device.acquisition_params.trigger_source == md.EXTERNAL

    def load_table(self, start, stop, wnums=None):
        LOG.debug('updating table')
        if wnums is None:
            self._acquisition = Acquisition(start, stop)
            wnums = np.linspace(1e7 / self._lambdas[start],
                                1e7 / self._lambdas[stop],
                                stop - start + 1)
        else:
            self._acquisition = Acquisition(1e7 / wnums[0], 1e7 / wnums[-1])

        LOG.debug('writing table')
        with self._lock:
            try:
                self.device.set_wavenumber_array(wnums)
            except MozzaError as e:
                try:
                    self.device.end_acquisition()
                    self.load_table(start, stop, wnums)
                except MozzaError:
                    LOG.error(e)
                    raise SpectroError("Error in loading spectral table {e}")
                else:
                    return

        self.buffer = np.zeros(self.device.get_raw_data_size(self.device.table_length),
                               dtype=np.uint8)

        if self.correct_amplitude is not None:
            # the only condition to create amp_correction array - the function exists!
            self.amp_correction = self.correct_amplitude(wnums)

    def read_raw(self):
        if self.device.acquisition_params.trigger_source == md.EXTERNAL:
            trigger_freq = float(self.device.get_trigger_frequency())
            if trigger_freq == 0:
                raise TriggerTimeoutError
            acq_time = self.buffer.size / trigger_freq / 64
            if acq_time > 1:
                # acquision by chuncks
                last = 0
                lp = self.device.table_length
                npts = int(round(trigger_freq / self.buffer.size * self.device.table_length * 64 - 1))
                while last < self.buffer.size:
                    N = min(npts, lp)
                    raw = self.device.read_raw(N)
                    self.buffer[last:last + len(raw)] = raw[0:len(raw)]
                    last += len(raw)
                    lp -= npts
                return np.ctypeslib.as_ctypes(self.buffer)
            else:
                raw = self.device.read_raw()
                return raw
        else:
            raw = self.device.read_raw()
            return raw

    def measure_offsets(self):
        with self._lock:
            signal_offset, reference_offset = self.device.measure_offsets(self.acquisition_params.signal_high_gain,
                                                                          self.acquisition_params.reference_high_gain)
        LOG.debug('measured offsets: signal %.2f, reference %.2f', signal_offset, reference_offset)
        self.process_params.signal_offset = signal_offset
        self.process_params.reference_offset = reference_offset
        # restore table and acquisition params
        self.device.set_rf_attenuation()
        self.device.set_acquisition_params()
        self.device.set_process_params()
        return signal_offset, reference_offset

    def _acquire_spectrum(self, background_mode=False):
        spec = None
        if (self._acquisition.start != self.acquisition.start or
                self._acquisition.stop != self.acquisition.stop):
            self.load_table(self.acquisition.start, self.acquisition.stop)

        if self._lock.acquire(False):
            try:
                self.device.begin_acquisition()
                raw = self.read_raw()
            except MozzaError as e:
                LOG.debug('Acquisition error: %s' % e)
            else:
                spec = np.array(self.device.process_spectrum(raw))
            finally:
                try:
                    self.device.end_acquisition()
                except MozzaError:
                    pass
                self._lock.release()
        else:
            return

        return spec * self.amp_correction if self.apply_amp_correction else spec

    def reset(self):
        pass

    def set_rf_attenuation(self, value):
        with self._lock:
            try:
                self.device.set_rf_attenuation(value)
            except MozzaError as e:
                raise SpectroError(f"Error in setting RF attenuation: {e}")

    @property
    def rf_attenuation(self):
        return self.device.rf_attenuation

    @property
    def acquisition_params(self):
        return self.device.acquisition_params

    @acquisition_params.setter
    def acquisition_params(self, params):
        self.device.acquisition_params = params

    @property
    def process_params(self):
        return self.device.process_params

    @process_params.setter
    def process_params(self, params):
        self.device.process_params = params

    def setup_gains(self):
        LOG.debug('setup gains')
        self.device.setup_gains(self.acquisition_params.signal_high_gain,
                                self.acquisition_params.reference_high_gain)

    def set_all_device_params(self):
        with self._lock:
            self.device.set_acquisition_params()
            self.device.set_process_params()
            self.device.set_rf_attenuation()
            self.setup_gains()

    def set_acquisition_params(self):
        try:
            self.device.set_acquisition_params()
        except MozzaError as e:
            raise SpectroError(f"Error in setting acquisition params: {e}")

    def set_process_params(self):
        try:
            self.device.set_process_params()
        except MozzaError as e:
            raise SpectroError(f"Error in setting process params: {e}")

    @property
    def ext_trigger_freq(self):
        with self._lock:
            return self.device.get_trigger_frequency()

    def acquire_raw(self):
        signal, reference = None, None
        with self._lock:
            try:
                nbytes = self.device.begin_acquisition()
                sleep(0.05)
                raw = self.read_raw()
            except MozzaError as e:
                LOG.debug('Acquisition error: %s' % e)
            else:
                signal, reference = (np.asarray(a) for a in self.device.separate_sig_ref(raw))
            finally:
                try:
                    self.device.end_acquisition()
                except MozzaError:
                    pass
        return signal, reference

    def set_auto_params(self, trigger_to_laser_us, acquisition_time_us=10):
        LOG.debug('setting auto params with trigger_to_laser_us=%d and acquisition_time_us=%d',
                  trigger_to_laser_us, acquisition_time_us)

        try:
            self.device.set_auto_params(self.acquisition_params.point_repetition,
                                        self.process_params.reference_offset,
                                        self.acquisition_params.signal_high_gain,
                                        self.acquisition_params.reference_high_gain,
                                        trigger_to_laser_us,
                                        acquisition_time_us)
        except MozzaError as e:
            raise SpectroError(f"Error in setting auto params {e}")

        return self.acquisition_params, self.process_params

    def load_amp_correction(self, serial_num):
        self.correct_amplitude = None  # reset the amp correction function
        amp_correction_path = Path('%04d_AmplitudeCorrection.txt' % serial_num)
        try:
            wavelength, amplitude = np.loadtxt(amp_correction_path, unpack=True)
        except OSError:
            LOG.warning('Amplitude correction file not found for Mozza#%d' % serial_num)
            self.apply_amp_correction = False
            return False
        else:
            # make amplitude correction function

            LOG.debug('Making amplitude correction.')
            if np.any(amplitude < 0):
                LOG.warning('Amplitude correction < 0 is not allowed!')
                self.apply_amp_correction = False
                return False
            elif len(amplitude) != len(wavelength):
                LOG.warning('Amplitude correction arrays are not the same size!')
                self.apply_amp_correction = False
                return False
            else:
                self.correct_amplitude = lambda x: np.interp(x, 1e7 / wavelength[::-1], amplitude[::-1])
                return True


class DAQ_0DViewer_MozzaSpectrometer(DAQ_Viewer_base):
    """
        =============== =========================
        **Attributes**  **Type**
        *params*        dictionnary list
        *hardware_averaging* boolean
        =============== =========================

        See Also
        --------
        DAQ_Viewer_base
    """

    params = comon_parameters + [
        {'title': 'Serial:', 'name': 'serial', 'type': 'list', 'values': DAQ_0DViewer_MozzaSpectro.get_serials()},
        {'title': 'Exposure Time:', 'name': 'exposure', 'type': 'float', 'value': 1.0},
    ]

    def ini_detector(self, controller=None):
        """
        Initialize the detector
        """
        self.status.update(initialized=False, info="", x_axis=None, y_axis=None, controller=None)

        try:
            self.controller = DAQ_0DViewer_MozzaSpectro()
            serial = self.settings.child('serial').value()
            self.controller.connect_device(serial)
            self.settings.child('exposure').setValue(self.controller.get_exposure())

            self.status.info = "Connected to Mozza Spectrometer"
            self.status.initialized = True
            self.status.controller = self.controller
            return self.status

        except Exception as e:
            self.emit_status(ThreadCommand("Update_Status", [str(e), "log"]))
            self.status.info = str(e)
            return self.status

    def close(self):
        """
        Terminate the communication protocol
        """
        self.controller.disconnect_device()

    def grab_data(self, Naverage=1, **kwargs):
        """
        Acquire data from the detector
        """
        try:
            data_tot = 0
            for _ in range(Naverage):
                data = self.controller._acquire_spectrum()
                data_tot += data
            data_tot /= Naverage
            self.data_grabed_signal.emit(
                [DataFromPlugins(name='Spectra', data=data_tot, dim='Data0D', labels=['Spectral Intensity'])])

        except Exception as e:
            self.emit_status(ThreadCommand("Update_Status", [str(e), "log"]))
            self.emit_status(ThreadCommand("Update_Status", ["Error while acquiring data", "log"]))

    def set_exposure(self):
        """
        Set the exposure time on the detector
        """
        exposure = self.settings.child('exposure').value()
        self.controller.set_exposure(exposure)
        self.settings.child('exposure').setValue(self.controller.get_exposure())

    def commit_settings(self, param):
        """
        Apply the settings when they are modified
        """
        if param.name() == 'exposure':
            self.set_exposure()
        elif param.name() == 'serial':
            self.controller.disconnect_device()
            self.controller.connect_device(param.value())

    def update_settings(self):
        """
        Update the available serial numbers in the settings
        """
        serials = DAQ_0DViewer_MozzaSpectro.get_serials()
        self.settings.child('serial').setOpts(limits=serials)


# If used as a standalone program
if __name__ == '__main__':
    main(__file__)

# import numpy as np
# from libmozza import mozza_defines as MD
# from libmozza.mozza import MozzaUSB, MozzaError
# from pymodaq.utils.daq_utils import ThreadCommand
# from pymodaq.utils.data import DataFromPlugins, DataToExport
# from pymodaq.control_modules.viewer_utility_classes import DAQ_Viewer_base, comon_parameters, main
# from pymodaq.utils.parameter import Parameter
# import struct
#
# class DAQ_0DViewer_MozzaSpectrometer(DAQ_Viewer_base):
#     """
#     Plugin pour le spectromètre Mozza.
#
#     Attributs:
#     -----------
#     controller: MozzaUSB
#         Objet permettant la communication avec le spectromètre Mozza.
#     """
#     params = comon_parameters + [
#         # Ajoutez ici les paramètres spécifiques à votre spectromètre
#     ]
#
#     def ini_attributes(self):
#         self.controller: MozzaUSB = None
#
#     def commit_settings(self, param: Parameter):
#         if param.name() == "un_parametre":
#             self.controller.methode_pour_appliquer_ce_changement()
#
#     def ini_detector(self, controller=None):
#         self.ini_detector_init(old_controller=controller, new_controller=MozzaUSB())
#
#         self.dte_signal_temp.emit(DataToExport(name='MozzaSpectrometer',
#                                                data=[DataFromPlugins(name='Spectre',
#                                                                     data=[np.array([0]), np.array([0])],
#                                                                     dim='Data0D',
#                                                                     labels=['Spectre', 'Label2'])]))
#
#         info = "Mozza Spectrometer initialisé"
#         initialized = True if self.controller else False
#         return info, initialized
#
#     def close(self):
#         self.controller.close()
#
#     def grab_data(self, Naverage=1, **kwargs):
#         with self.controller as mozza:
#             serials = mozza.get_serials()
#             if serials:
#                 mozza.connect(serial=serials[0])
#
#                 wls_nm = (2000, 6000)
#                 wnums = np.arange(1e7 / wls_nm[1], 1e7 / wls_nm[0], 5)
#                 mozza.set_wavenumber_array(wnums)
#                 mozza.acquisition_params.trigger_source = MD.INTERNAL
#                 mozza.acquisition_params.trigger_frequency_Hz = 10000
#                 mozza.set_auto_params()
#
#                 bytes_to_read = mozza.begin_acquisition()
#                 try:
#                     raw = mozza.read_raw()
#                 except MozzaError as e:
#                     self.emit_status(ThreadCommand('Update_Status', [str(e)]))
#                     return
#                 signal, reference = mozza.separate_sig_ref(raw)
#                 try:
#                     mozza.end_acquisition()
#                 except MozzaError as e:
#                     self.emit_status(ThreadCommand('Update_Status', [str(e)]))
#                     return
#                 spectrum = mozza.process_spectrum(sig_data=signal, ref_data=reference)
#
#                 self.dte_signal.emit(DataToExport(name='MozzaSpectrometer',
#                                                   data=[DataFromPlugins(name='Spectre', data=[spectrum],
#                                                                         dim='Data0D', labels=['Spectre'])]))
#             else:
#                 self.emit_status(ThreadCommand('Update_Status', ['No Mozza device is found']))
#
#     def callback(self):
#         pass  # Cette méthode est optionnelle pour votre cas
#
#     def stop(self):
#         try:
#             self.controller.end_acquisition()
#         except MozzaError as e:
#             self.emit_status(ThreadCommand('Update_Status', [str(e)]))
#         self.emit_status(ThreadCommand('Update_Status', ['Acquisition arrêtée']))
#         return ''
#
# if __name__ == '__main__':
#     main(__file__)