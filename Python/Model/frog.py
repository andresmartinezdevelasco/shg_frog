"""
Model for the FROG setup

File name: frog.py
Author: Julian Krauth
Date created: 2019/12/02
Python Version: 3.7
"""
import pathlib
from time import sleep, strftime
from datetime import datetime
from collections import namedtuple
import numpy as np
import yaml
import imageio
from pyqtgraph.parametertree import Parameter

from labdevices import newport

from . import acquisition
from . import phase_retrieval

MAIN_DIR = pathlib.Path(__file__).parents[2]
CONFIG_DIR = MAIN_DIR / "Config" / 'config.yml'
DATA_DIR = MAIN_DIR / "Data"

SPEEDOFLIGHT = 299792458. #m/s

Data = namedtuple('Data', ['image', 'meta'])

class FROG:
    """Top level class for the FROG experiment definition."""

    def __init__(self, test: bool=True):
        """
        Arguments:
        test -- whether to run with dummy devices for testing or not.
        """
        # Load the FROG devices (optional: virtual devices for testing)
        if test:
            self.stage = newport.SMC100DUMMY(port='/dev/ttyUSB0', dev_number=1)
        else:
            self.stage = newport.SMC100(port='/dev/ttyUSB0', dev_number=1)
        self.spect = acquisition.Spectrometer(test)

        self.files = FileHandler()
        self._config = self.files.get_main_config()

        # Will contain the measurement data and settings
        self._data = None

        self.stop_measure = False

        self.algo = phase_retrieval.PhaseRetrieval()
        self.parameters = FrogParams(self._config['pxls width'], self._config['pxls height'])

    def initialize(self, mode: int=0) -> None:
        """Connect to the devices."""
        self.stage.initialize()
        self.spect.initialize(mode)

    def measure(self, sig_progress, sig_measure):
        """Carries out the measurement loop."""
        # Get measurement settings
        meta = self._get_settings()
        # Delete possible previous measurement data.
        self._data = None
        # Move stage to Start Position and wait for end of movement
        self.stage.move_abs(meta['start position'])
        self.stage.wait_move_finish(1.)
        for i in range(meta['step number']):
            print("Loop...")
            # Move stage
            self.stage.move_abs(meta['start position']+i*meta['step size'])
            self.stage.wait_move_finish(0.2)
            # Record spectrum
            y_data = self.spect.get_spectrum()
            # Create 2d frog-array to fill with data
            if i==0:
                frog_array = np.zeros((len(y_data), meta['step number']))
            # Stitch data together
            frog_array[:,i] = y_data
            # Send data to plot
            sig_measure.emit(3, y_data)
            sig_measure.emit(2, frog_array)
            sleep(0.2)
            sig_progress.emit(i+1)
            if self.stop_measure:
                print("Measurement aborted, data discarded!")
                break
        if not self.stop_measure:
            # Save Frog trace and measurement settings as instance attributes,
            # they are then available for save button of GUI.
            frog_trace = self.scale_pxl_values(frog_array)
            # maybe add possibility to add a comment to the meta data at
            # end of measurement.
            self._data = Data(frog_trace, meta)
            print("Measurement finished!")
        else:
            self.stop_measure = False

    def _get_settings(self) -> dict:
        """Returns the settings for the current measurement as dictionary.
        Everything listed here will be saved in the metadata .yml file."""
        date = datetime.now().strftime('%Y-%m-%d')
        time = datetime.now().strftime('%H:%M:%S')
        step_size = self.parameters.get_step_size()
        # Time step per pixel in ps
        ccddt = 1e6*2*step_size/(SPEEDOFLIGHT)
        ccddv = self.freq_step_per_pixel()
        # in future maybe write also exposure time, gain, max Intensity, bit depth
        settings = {
            'date': date,
            'time': time,
            'center position': self.parameters.get_center_position(),
            'start position': self.parameters.get_start_position(),
            'step number': self.parameters.get_step_num(),
            'bit depth': self.spect.camera.pix_format(),
            'step size': step_size,
            'ccddt': ccddt,
            'ccddv': ccddv,
        }
        return settings

    def scale_pxl_values(self, frog_array):
        """Maximize contrast of the image"""
        if self.spect.mode == 0: # for ccd/cmos camera
            # Scale image according to bit depth
            pix_format = self.spect.camera.pix_format()
            if pix_format == 'Mono8':
                scale = 255.
            elif pix_format == 'Mono12':
                scale = 65535.
            frog_array_scaled = np.rint(scale * frog_array / np.amax(frog_array)).astype(int)
        elif self.spect.mode == 1: # for ANDO
            raise Exception("scaling for ando not implemented yet.")
            # Maybe there is no scaling needed...
        return frog_array_scaled

    def freq_step_per_pixel(self):
        """Returns the frequency step per bin/pixel of the taken trace.
        Needed for phase retrieval.
        """
        if self.spect.mode == 0: # for CCD camera
            wlatcenter = self._config['center wavelength']
            # Wavelength step per pixel:
            # I assume that over the size of the CCD chip
            # (for small angles) the wavelength scale is linear
            # The number is calculated using the wavelength spread per mrad
            # specified for the grating.
            # This is then divided by the number of pixels which fit
            # into a 1mrad range at the focal distance of the lens:
            # Grating spec: 0.81nm/mrad => 0.81nm/0.2mm (for a 200mm focal lens)
            # =>0.81nm/34.13pixels (for 5.86micron pixelsize)
            mm_per_mrad = 1. * self._config['focal length'] / 1000.
            pxls_per_mrad = mm_per_mrad/(self._config['pixel size'] \
                /1000) # yields 34
            nm_per_px = self._config['grating']/pxls_per_mrad # yields 0.0237nm
            # Frequency step per pixel
            vperpxGHz = SPEEDOFLIGHT * (1/(wlatcenter) \
                -1/(wlatcenter + nm_per_px)) # GHz
            vperpx = vperpxGHz * 1.e-3 # THz
            # Also here I assume that for small angles the frequency can be
            # considered to be linear on the CCD plane.

        elif self.spect.mode == 1: # for ANDO spectrometer
            # One has to get that information from the ando settings.
            raise Exception("Calibration for ANDO spectrometer not yet implemented!")
        return vperpx

    def retrieve_phase(self, sig_retdata, sig_retlabels, sig_rettitles, sig_retaxis):
        """Execute phase retrieval algorithm."""
        if self._data is not None:
            ccddt = self._data.meta['ccddt']
            ccddv = self._data.meta['ccddv']
            # the following should also get into meta data and be read from
            # there. This will allow for loading files.
            pixels = self.parameters.get_prep_frog_size()
            gtol = self.parameters.get_error_tolerance()
            itermax = self.parameters.get_max_iterations()

            self.algo.prepFROG(ccddt=ccddt, ccddv=ccddv, N=pixels, \
                ccdimg=self._data.image, flip=2)
            self.algo.retrievePhase(GTol=gtol, iterMAX=itermax, \
                signal_data=sig_retdata, signal_label=sig_retlabels, \
                    signal_title=sig_rettitles, signal_axis=sig_retaxis)
        else:
            raise Exception('No recorded trace in buffer!')

    def save_measurement_data(self):
        if self._data is None:
            print('No data saved, do a measurement first!')
            return
        self.files.save_new_measurement(self._data, self._config)
        print('All data saved!')

    def load_measurement_data(self, path):
        self._data = self.files.load_measurement(path)

    def close(self):
        """Close connection with devices."""
        self.stage.close()
        self.spect.close()



class FrogParams:
    """
    Class which implements the parameters used by the parametertree widget.
    """

    def __init__(self, sensor_width: int, sensor_height: int):
        """
        The two arguments are needed to set the limits of the ROI
        parameters correctly.
        Arguments:
        sensor_width -- pixels along horizontal
        sensor_height -- pixels along vertical
        """
        # Define parameters for parametertree
        self._sensor_width = sensor_width
        self._sensor_height = sensor_height
        params = [
            {'name':'Phase Retrieval', 'type':'group', 'visible':True, 'children': [
                {'name':'prepFROG Size', 'type': 'int', 'value': 128},
                {'name':'Seed', 'type': 'list', 'values': {"Random":0,"fromFile":1}, 'value':0},
                {'name':'Max. Iterations', 'type': 'int', 'value': 200},
                {'name':'G Tolerance', 'type': 'float', 'value': 0.001}
            ]},
            {'name':'ANDO Spectrometer', 'type':'group', 'visible':False, 'children': [
                {'name':'Center', 'type': 'float', 'value': 390.},
                {'name':'Span', 'type': 'float', 'value': 20.},
                {'name':'CW mode', 'type': 'bool', 'value': True},
                {'name':'Rep. time', 'type': 'float', 'value': 36},
                {'name':'Sampling', 'type': 'int', 'value': 201}
            ]},
            {'name':'ALLIED VISION CCD', 'type':'group', 'visible':False, 'children': [
                {'name':'Exposure', 'type': 'float', 'value': 0.036, 'dec': True, \
                    'step': 1, 'siPrefix': True, 'suffix': 's'},
                {'name':'Gain', 'type': 'float', 'value': 0, 'dec': False, 'step': 1},
                {'name':'Crop Image', 'type': 'group', 'expanded':False, 'children': [
                    {'name':'Width','type':'int', \
                        'value': 600,'limits':[1, sensor_width],'step': 2},
                    {'name':'Height','type':'int', \
                        'value': 10,'limits':[1, sensor_height],'step': 2},
                    {'name':'Xpos','type':'int','value': 400,'step': 2},
                    {'name':'Ypos','type':'int','value': 470,'step': 2}
                ]},
                {'name':'Trigger', 'type': 'group', 'children': [
                    {'name':'Mode','type':'list','visible':False, \
                        'values': {"On":1,"Off":0},'value':1},
                    {'name':'Source','type':'list','visible':True, \
                        'values': {
                            "Freerun":'Freerun',
                            "External":'Line1'
                            #'Line2':'Line2',
                            #'FixedRate':'FixedRate',
                            #'Software':'Software'
                        },'value':'External'}
                ]},
                {'name':'PixelFormat', 'type': 'list', \
                    'values': {
                        'Mono8':'Mono8',
                        'Mono12':'Mono12'
                        #'Mono12Packed':'Mono12Packed'
                    }, 'value':'Mono8'},
                ]},
            {'name':'Newport Stage', 'type':'group', 'visible':False, 'children': [
                {'name':'Position', 'type':'float', 'value': 0., 'readonly': True},
                {'name':'GoTo', 'type':'float', 'value': 0.},
                {'name':'Offset', 'type':'float', 'value': 11370, 'limits':[0,25000]},
                {'name':'Start Position', 'type':'float', 'value': -256},
                {'name':'Step Size', 'type':'float', 'value': 4.},
                {'name':'Number of steps', 'type':'int', 'readonly':True, 'value': 128}
            ]}
        ]

        # Create parameter objects
        self.par = Parameter.create(name='params',
                              type='group',
                              children=params)

        ### Some settings regarding CCD parameters ###
        # Create limits for crop settings

        crop_par = self.par.param('ALLIED VISION CCD').child('Crop Image')
        width_par = crop_par.child('Width')
        height_par = crop_par.child('Height')
        xpos_par = crop_par.child('Xpos')
        ypos_par = crop_par.child('Ypos')
        width_par.setLimits([1, self._sensor_width-xpos_par.value()])
        height_par.setLimits([1, self._sensor_height-ypos_par.value()])
        xpos_par.setLimits([0, self._sensor_width-width_par.value()])
        ypos_par.setLimits([0, self._sensor_height-height_par.value()])
        crop_par.sigTreeStateChanged.connect(self.set_crop_limits)

        ### Some settings regarding the Stage parameters ###
        stage_par = self.par.param('Newport Stage')
        start_par = stage_par.child('Start Position')
        step_par = stage_par.child('Step Size')
        off_par = stage_par.child('Offset')

        # Set limits of Start Position, depending on offset
        start_par.setLimits([-off_par.value(),-0.2])
        off_par.sigValueChanged.connect(self.set_start_pos_limits)

        # Set limits of Step Size, depending on Start Position
        step_par.setLimits([0.2,abs(start_par.value())])
        start_par.sigValueChanged.connect(self.set_step_limits)

        # Always update number of steps, given by start pos and step size
        start_par.sigValueChanged.connect(self.show_steps)
        step_par.sigValueChanged.connect(self.show_steps)

    def set_crop_limits(self, param, changes):
        max_width, max_height = self.get_sensor_size()
        for param, change, data in changes:
            path = self.par.childPath(param)
            par = self.par.param(path[0]).child(path[1])
            if path[2]=='Width':
                par.child('Xpos').setLimits([0, max_width-par.child(path[2]).value()])
            elif path[2]=='Height':
                par.child('Ypos').setLimits([0, max_height-par.child(path[2]).value()])
            elif path[2]=='Xpos':
                par.child('Width').setLimits([1, max_width-par.child(path[2]).value()])
            elif path[2]=='Ypos':
                par.child('Height').setLimits([1, max_height-par.child(path[2]).value()])

    def get_crop_par(self):
        """ Get the crop parameters from parameter tree"""
        roi_par = self.par.param('ALLIED VISION CCD').child('Crop Image')
        xpos = roi_par.child('Xpos').value()
        ypos = roi_par.child('Ypos').value()
        width = roi_par.child('Width').value()
        height = roi_par.child('Height').value()
        return xpos, ypos, width, height

    def update_crop_param(self, roi):
        """Used as action when changing roi in roi window"""
        pos = roi.pos()
        size = roi.size()
        # Update the CROP parameters regarding region of interest
        roi_par = self.par.param('ALLIED VISION CCD').child('Crop Image')
        # Create even numbers. Odd numbers crash with some cameras
        # and make sure that offset and size stays in allowed range
        max_size = self.get_sensor_size()
        for i in range(2):
            if pos[i] < 0:
                pos[i] = 0
            if size[i] > max_size[i]:
                size[i] = max_size[i]
                pos[i] = 0
            if size[i]+pos[i] > max_size[i]:
                size[i] = max_size[i] - pos[i]
            pos[i] = round(pos[i]/2.)*2
            size[i] = round(size[i]/2.)*2
        roi_par.child('Xpos').setValue(int(pos[0]))
        roi_par.child('Ypos').setValue(int(pos[1]))
        roi_par.child('Width').setValue(int(size[0]))
        roi_par.child('Height').setValue(int(size[1]))

    def set_step_limits(self, param, val):
        step_par = self.par.param('Newport Stage').child('Step Size')
        step_par.setLimits([0.2,abs(val)])

    def set_start_pos_limits(self, param, val):
        start_pos = self.par.param('Newport Stage').child('Start Position')
        start_pos.setLimits([-val,-0.2])

    def show_pos(self, val):
        pos = self.par.param('Newport Stage').child('Position')
        pos.setValue(val)

    def show_steps(self, dummy):
        start_pos = self.par.param('Newport Stage').child('Start Position')
        step_size = self.par.param('Newport Stage').child('Step Size')
        val = int(round(2*abs(start_pos.value())/step_size.value()))

        num = self.par.param('Newport Stage').child('Number of steps')
        num.setValue(val)

    def print_par_changes(self, val: bool=True):
        if val:
            # Do print changes in parametertree
            self.par.sigTreeStateChanged.connect(self._change)
        else:
            self.par.sigTreeStateChanged.disconnect(self._change)

    def _change(self, param, changes):
        ## If anything changes in the parametertree, print a message
        for param, change, data in changes:
            path = self.par.childPath(param)
            if path is not None:
                child_name = '.'.join(path)
            else:
                child_name = param.name()
            print("tree changes:")
            print('  parameter: %s'% child_name)
            print('  change:    %s'% change)
            print('  data:      %s'% str(data))
            print('  ----------')


    def get_sensor_size(self) -> list():
        return self._sensor_width, self._sensor_height

    def get_start_position(self) -> float:
        return self.par.param('Newport Stage').child('Start Position').value()

    def get_step_num(self) -> int:
        return self.par.param('Newport Stage').child('Number of steps').value()

    def get_step_size(self) -> float:
        return self.par.param('Newport Stage').child('Step Size').value()

    def get_center_position(self) -> float:
        return self.par.param('Newport Stage').child('Offset').value()

    def get_prep_frog_size(self) -> int:
        return self.par.param('Phase Retrieval').child('prepFROG Size').value()

    def get_error_tolerance(self) -> int:
        return self.par.param('Phase Retrieval').child('G Tolerance').value()

    def get_max_iterations(self) -> int:
        return self.par.param('Phase Retrieval').child('Max. Iterations').value()


class FileHandler:
    def __init__(self):
        self.name_meta = 'meta.yml'
        self.name_config = 'config.yml'
        self.name_frog = 'frog.tiff'

    def _get_new_measurement_path(self) -> pathlib.Path:
        """Returns a path for the next measurement."""
        today = strftime("%Y%m%d")
        today_path = DATA_DIR / today
        new_path = self.get_unique_path(today_path, 'measurement_{:03d}')
        return new_path

    def get_unique_path(self, directory: pathlib.Path, name_pattern: str) -> pathlib.Path:
        counter = 0
        while True:
            counter += 1
            path = directory / name_pattern.format(counter)
            if not path.exists():
                return path

    def save_new_measurement(self, data, config):
        """ Saves data and configuration into a new measurement folder """
        # Get unique path for new measurement
        measurement_path = self._get_new_measurement_path()
        measurement_path.mkdir(parents=True)
        # Save Frog image with correct bit depth
        if data.meta['bit depth'] == 'Mono8':
            bit_type = np.uint8
        elif data.meta['bit depth'] == 'Mono12':
            bit_type = np.uint16
        imageio.imsave(measurement_path / self.name_frog, data.image.astype(bit_type))
        # Save settings
        with open(measurement_path / self.name_meta, 'w') as f:
            f.write(yaml.dump(data.meta, default_flow_style=False))
        # Save configuration
        with open(measurement_path / self.name_config, 'w') as f:
            f.write(yaml.dump(config, default_flow_style=False))

    def get_main_config(self) -> dict:
        """Get defaults from configuration file."""
        with open(CONFIG_DIR, 'r') as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
        return config

    def load_measurement(self, measurement_path: pathlib.Path):
        """ Get config, settings (meta), and image data of an old measurement. """
        # Load settings
        with open(measurement_path / self.name_meta, 'r') as f:
            meta = yaml.load(f, Loader=yaml.FullLoader)
        # Load frog image
        frog_image = imageio.imread(measurement_path / self.name_frog)
        data = Data(frog_image, meta)
        # Load configuration
        #with open(measurement_path / self.name_config, 'r') as f:
        #    config = yaml.load(f, Loader=yaml.FullLoader)
        return data