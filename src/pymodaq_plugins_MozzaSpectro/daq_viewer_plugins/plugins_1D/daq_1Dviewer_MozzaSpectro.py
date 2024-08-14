from qtpy import QtWidgets
from pymodaq.control_modules.viewer_utility_classes import DAQ_Viewer_base, comon_parameters, main
import numpy as np
from pymodaq.utils.daq_utils import ThreadCommand, getLineInfo
from pymodaq.utils.data import DataFromPlugins, Axis, DataToExport
import sys
import time
from libmozza import mozza_defines as MD
from libmozza.mozza import MozzaUSB, MozzaError


class DAQ_1DViewer_MozzaSpectro(DAQ_Viewer_base):
    """PyMoDAQ plugin controlling Mozza spectrometers using the Mozza SDK"""

    params = comon_parameters + [
        {'title': 'Trigger Frequency (Hz):', 'name': 'trigger_freq', 'type': 'int', 'value': 10000},
        {'title': 'Wavenumber Start:', 'name': 'wavenumber_start', 'type': 'float', 'value': 2000},
        {'title': 'Wavenumber End:', 'name': 'wavenumber_end', 'type': 'float', 'value': 6000},
    ]

    def ini_attributes(self):
        # print("Initializing attributes...")
        self.controller = None
        self.serials = []

    def commit_settings(self, param):
        # print(f"Committing settings for {param.name()}")
        if param.name() in ['trigger_freq', 'wavenumber_start', 'wavenumber_end']:
            print("Reinitializing controller due to parameter change")
            self.initialize_controller()

    def ini_detector(self, controller=None):
        # print("Starting ini_detector method...")
        if self.settings['controller_status'] == "Slave":
            # print("Controller status: Slave")
            if controller is None:
                print("No controller has been defined externally while this axe is a slave one")
                raise Exception('No controller has been defined externally while this axe is a slave one')
            else:
                print("Controller defined externally")
                self.controller = controller
        else:  # Master stage
            # print("Controller status: Master")
            self.initialize_controller()
            if self.controller is None:
                print("Initialization failed: Controller is None")
                return 'Initialization failed', False

        initialized = True
        info = 'Detector initialized successfully'
        # print(info)
        return info, initialized

    def initialize_controller(self):
        try:
            # print("Attempting to initialize controller...")
            self.controller = MozzaUSB()
            self.serials = self.controller.get_serials()
            if self.serials:
                # print(f'Found Mozza device with serials: {self.serials}')
                self.controller.connect(serial=self.serials[0])
                print(f'Connected to Mozza device with serial: {self.serials[0]}')

                wls_nm = (self.settings.child('wavenumber_start').value(), self.settings.child('wavenumber_end').value())
                # print(f"Setting wavenumber array from {wls_nm[0]} to {wls_nm[1]}")
                self.wnums = np.arange(1e7 / wls_nm[1], 1e7 / wls_nm[0], 5)
                self.controller.set_wavenumber_array(self.wnums)
                self.controller.acquisition_params.trigger_source = MD.INTERNAL
                self.controller.acquisition_params.trigger_frequency_Hz = self.settings.child('trigger_freq').value()
                self.controller.set_auto_params()

                freq_kHz = self.controller.get_trigger_frequency() * 1e-3
                # print(f'Mozza external trigger frequency set to: {freq_kHz:.1f} kHz')
                # print('Connected to Mozza device')
            else:
                print('No Mozza device found')
                self.controller = None
        except MozzaError as e:
            print(f"Failed to connect to Mozza device: {e}")
            self.controller = None
        except Exception as e:
            print(f"Unexpected error during initialization: {e}")
            self.controller = None

    def get_xaxis(self, ind_spectro):
        try:
            # print("Getting wavenumber array...")
            return self.wnums
        except Exception as e:
            print(f"Failed to get wavenumber array: {e}")
            return np.array([])

    def close(self):
        if self.controller is not None:
            try:
                print("Ending acquisition and disconnecting controller...")
                self.controller.end_acquisition()
            except MozzaError as e:
                print(f"Failed to end acquisition: {e}")
            except Exception as e:
                print(f"Unexpected error during disconnection: {e}")
            self.controller.disconnect()

    def grab_data(self, Naverage=1, **kwargs):
        dte = DataToExport('Mozza')

        if self.controller:
            try:
                # print("Beginning data acquisition...")
                bytes_to_read = self.controller.begin_acquisition()
                # print(f"Bytes to read: {bytes_to_read}")
                raw = self.controller.read_raw()
                # print("Raw data read successfully")
                signal, reference = self.controller.separate_sig_ref(raw)
                # print("Signal and reference separated")
                self.controller.end_acquisition()
                # ("Acquisition ended")
                spectrum = self.controller.process_spectrum(sig_data=signal, ref_data=reference)
                data = self.convert_to_numpy_array(spectrum, len(self.wnums))
                # print("Spectrum processed")

                wnums = self.get_xaxis(0)


                dte.append(DataFromPlugins(name='Mozza', data=[data], dim='Data1D',
                                           axes=[Axis(data=wnums, label='Wavenumber', units='cm^-1')]))
                QtWidgets.QApplication.processEvents()
            except MozzaError as e:
                print(f"Failed to acquire data: {e}")
            except Exception as e:
                print(f"Unexpected error during data acquisition: {e}")

        self.dte_signal.emit(dte)

    def convert_to_numpy_array(self, c_array, length):
        """Convert a C array to a NumPy array."""
        # Convert C array to a NumPy array
        return np.ctypeslib.as_array(c_array, shape=(length,))

    def stop(self):
        if self.controller is not None:
            try:
                print("Stopping data acquisition...")
                self.controller.end_acquisition()
            except MozzaError as e:
                print(f"Failed to stop acquisition: {e}")
            except Exception as e:
                print(f"Unexpected error during stop: {e}")

if __name__ == '__main__':
    main(__file__)
