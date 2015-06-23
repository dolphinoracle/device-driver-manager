#! /usr/bin/env python3

# from gi.repository import Gtk, GdkPixbuf, GObject, Pango, Gdk, GLib
from gi.repository import Gtk, GObject, GLib
from os.path import join, abspath, dirname, basename, exists
from utils import ExecuteThreadedCommands, hasInternetConnection, \
                  getoutput, getPackageVersion
import os
import re
from glob import glob
from dialogs import MessageDialogSafe
from treeview import TreeViewHandler
from queue import Queue
from logger import Logger

# i18n: http://docs.python.org/3/library/gettext.html
import gettext
from gettext import gettext as _
gettext.textdomain('ddm')

# Need to initiate threads for Gtk
GObject.threads_init()


#class for the main window
class DDM(object):

    def __init__(self):

        # Load window and widgets
        self.scriptName = basename(__file__)
        self.scriptDir = abspath(dirname(__file__))
        self.mediaDir = join(self.scriptDir, '../../share/ddm')
        self.builder = Gtk.Builder()
        self.builder.add_from_file(join(self.mediaDir, 'ddm.glade'))

        # Main window objects
        go = self.builder.get_object
        self.window = go("ddmWindow")
        self.tvDDM = go("tvDDM")
        self.btnSave = go("btnSave")
        self.btnHelp = go("btnHelp")
        self.btnQuit = go("btnQuit")
        self.pbDDM = go("pbDDM")

        self.window.set_title(_("Device Driver Manager"))
        self.btnSave.set_label(_("Install"))
        self.btnHelp.set_label(_("Help"))
        self.btnQuit.set_label(_("Quit"))

        # Initiate variables
        self.queue = Queue(-1)
        self.threads = {}
        self.hardware = []
        self.loadedDrivers = []
        self.notSupported = []
        self.paeBooted = False
        self.helpFile = join(self.scriptDir, "html/help.html")
        self.logFile = '/var/log/ddm.log'
        self.log = Logger(self.logFile, addLogTime=False, maxSizeKB=5120)
        self.tvDDMHandler = TreeViewHandler(self.tvDDM)
        self.tvDDMHandler.connect('checkbox-toggled', self.tv_checkbox_toggled)

        # Connect builder signals and show window
        self.builder.connect_signals(self)
        self.window.show_all()

        # Fill treeview
        self.fill_treeview_ddm()

    # ===============================================
    # Main window functions
    # ===============================================

    def on_btnSave_clicked(self, widget):
        # Save selected hardware
        command = ''
        model = self.tvDDM.get_model()
        itr = model.get_iter_first()
        while itr is not None:
            action = 'no change'
            selected = model.get_value(itr, 0)
            device = model.get_value(itr, 2)
            manufacturerId = ''

            # Check currently selected state with initial state
            # This decides whether we should install or purge the drivers
            for hw in self.hardware:
                self.log.write("Device = {} in {}".format(device, hw[2]), 'on_btnSave_clicked')
                if device in hw[2]:
                    manufacturerId = hw[4]
                    if hw[0] and not selected:
                        action = 'purge'
                    elif not hw[0] and selected:
                        action = 'install'
                    break

            self.log.write("{}: {} ({})".format(action, device, manufacturerId), 'on_btnSave_clicked')

            # Install/purge selected driver
            if action == 'install':
                # Check if there is an internet connection
                if not hasInternetConnection():
                    title = _("No internet connection")
                    msg = _("You need an internet connection to install the additional software.\n"
                            "Please, connect to the internet and try again.")
                    MessageDialogSafe(title, msg, Gtk.MessageType.WARNING, self.window).show()
                    break

                # Run the manufacturer specific bash script
                if manufacturerId == '1002':
                    command += 'install-ati; '
                elif manufacturerId == '10de':
                    command += 'install-nvidia; '
                elif manufacturerId == '14e4':
                    command += 'install-broadcom; '
                elif 'pae' in manufacturerId:
                    command += 'install-pae; '

            elif action == 'purge':
                if 'pae' in manufacturerId:
                    command += 'install-pae purge; '
                elif manufacturerId == '14e4':
                    command += 'install-broadcom purge; '
                else:
                    # Install the default open driver (includes cleanup propietary drivers and configuration)
                    command += 'install-open; '

            # Get the next in line
            itr = model.iter_next(itr)

        # Execute the command
        if command != '':
            self.log.write("Command to execute: {}".format(command), 'on_btnSave_clicked')
            self.exec_command(command)

    def on_btnQuit_clicked(self, widget):
        self.on_ddmWindow_destroy(widget)

    def on_btnHelp_clicked(self, widget):
        # Open the help file as the real user (not root)
        logname = getoutput('logname')[0]
        ff = '/opt/firefox/firefox'
        if exists(ff):
            os.system("su {} -c \"{} {}\" &".format(logname, ff, self.helpFile))
        else:
            # If Firefox was removed, this might work
            os.system("su {} -c \"xdg-open {}\" &".format(logname, self.helpFile))

    def get_supported_hardware(self):
        # Fill self.hardware
        self.hardware = []

        # First row are column names
        self.hardware.append([_("Install"), '', _("Device"), 'driver', 'manid', 'deviceid'])

        # Get hardware information
        self.get_ati()
        self.get_nvidia()
        self.get_broadcom()
        self.get_pae()

    # This method is fired by the TreeView.checkbox-toggled event
    def tv_checkbox_toggled(self, obj, path, colNr, toggleValue):
        path = int(path)
        model = self.tvDDM.get_model()
        itr = model.get_iter(path)
        driver = model[itr][2].lower()

        if 'pae' in driver and not toggleValue and self.paeBooted:
            title = _("Remove kernel")
            msg = _("You cannot remove a booted kernel.\nPlease, boot another kernel and try again.")
            self.log.write(msg, 'tv_checkbox_toggled')
            MessageDialogSafe(title, msg, Gtk.MessageType.WARNING, self.window).show()
            model[itr][0] = True

    def fill_treeview_ddm(self):
        # Fill a list with supported hardware
        self.get_supported_hardware()

        # columns: checkbox, image (logo), device, driver
        columnTypes = ['bool', 'GdkPixbuf.Pixbuf', 'str']

        # Keep some info from the user
        showHw = []
        for hw in self.hardware:
            showHw.append([hw[0], hw[1], hw[2]])

        # Fill treeview
        self.tvDDMHandler.fillTreeview(contentList=showHw, columnTypesList=columnTypes, firstItemIsColName=True, fontSize=12000)

        # Show message if nothing is found or hardware is not supported
        title = _("Hardware scan")
        if self.notSupported:
            if len(self.hardware) < 2:
                self.set_buttons_state(False)
            msg = _("There are no available drivers for your hardware:")
            msg = "{}\n\n{}".format(msg, '\n'.join(self.notSupported))
            self.log.write(msg, 'fill_treeview_ddm')
            MessageDialogSafe(title, msg, Gtk.MessageType.WARNING, self.window).show()
        elif len(self.hardware) < 2:
            self.set_buttons_state(False)
            msg = _("DDM did not find any supported hardware.")
            self.log.write(msg, 'fill_treeview_ddm')
            MessageDialogSafe(title, msg, Gtk.MessageType.INFO, self.window).show()

    def exec_command(self, command):
        try:
            # Run the command in a separate thread
            self.set_buttons_state(False)
            name = 'cmd'
            t = ExecuteThreadedCommands([command], self.queue)
            self.threads[name] = t
            t.daemon = True
            t.start()
            self.queue.join()
            GLib.timeout_add(250, self.check_thread, name)

        except Exception as detail:
            MessageDialogSafe(self.btnSave.get_label(), detail, Gtk.MessageType.ERROR, self.window).show()

    def set_buttons_state(self, enable):
        if not enable:
            # Disable buttons
            self.btnSave.set_sensitive(False)
        else:
            # Enable buttons and reset progress bar
            self.btnSave.set_sensitive(True)
            self.pbDDM.set_fraction(0)

    def check_thread(self, name):
        if self.threads[name].is_alive():
            self.pbDDM.pulse()
            if not self.queue.empty():
                ret = self.queue.get()
                self.log.write("Queue returns: {}".format(ret), 'check_thread')
                self.queue.task_done()
                self.show_message(ret, True)
            return True

        # Thread is done
        self.log.write(">> Thread is done", 'check_thread')
        if not self.queue.empty():
            ret = self.queue.get()
            self.queue.task_done()
            self.show_message(ret, True)
        del self.threads[name]

        self.set_buttons_state(True)

        title = _("Saved")
        msg = _("You will need to restart your system.")
        MessageDialogSafe(title, msg, Gtk.MessageType.INFO, self.window).show()

        return False

    # Close the gui
    def on_ddmWindow_destroy(self, widget):
        # Close the app
        Gtk.main_quit()

    # ===============================================
    # Hardware functions
    # ===============================================

    def get_ati(self):
        manufacturerId = '1002'
        startSeries = 5000
        deviceArray = self.get_lspci_info(manufacturerId, 'VGA')

        # TESTING - Uncomment the following line for testing:
        #deviceArray = [['Advanced Micro Devices [AMD] nee ATI Manhattan [Mobility Radeon HD 5400 Series]', '68e0']]
        #deviceArray = [['Advanced Micro Devices, Inc. [AMD/ATI] RV710 [Radeon HD 4350/4550]', '68e0']]
        #deviceArray = [['Advanced Micro Devices [AMD/ATI] RS880 [Radeon HD 4290]', '68e0']]

        if deviceArray:
            self.log.write("Device(s): {}".format(deviceArray), 'get_ati')
            # Check if fglrx is loaded
            # If it is: checkbox is selected
            loadedDrv = self.get_loaded_graphical_driver()
            self.log.write("Loaded graphical driver: {}".format(loadedDrv), 'get_ati')

            # Get the manufacturer's logo
            logo = join(self.mediaDir, 'images/ati.png')

            # Fill the hardware array
            for device in deviceArray:
                self.log.write("ATI device found: {}".format(device[0]), 'get_ati')
                # Check if ATI series is above 5000
                matchObj = re.search('HD\W([0-9]{4})', device[0])
                if matchObj:
                    shortDevice = self.shorten_long_string(device[0], 50)
                    series = int(matchObj.group(1))
                    self.log.write("ATI HD series: {}".format(series), 'get_ati')
                    driver = 'fglrx'

                    # Check the series
                    #if series >= 1000 and series < startSeries:
                        ## Too old: use open Radeon drivers
                        #driver = 'radeon'

                    # Don't show older ATI cards
                    if series < startSeries:
                        break

                    self.log.write("ATI driver to use: {}".format(driver), 'get_ati')

                    # Check if the available driver is already loaded
                    selected = False
                    if loadedDrv == driver:
                        selected = True

                    # Fill self.hardware
                    self.hardware.append([selected, logo, shortDevice, driver, device[1], device[2]])
                else:
                    self.notSupported.append(device[0])

    def get_nvidia(self):
        manufacturerId = '10de'
        deviceArray = self.get_lspci_info(manufacturerId, 'VGA')

        # TESTING - Uncomment the following line for testing:
        #deviceArray = [['NVIDIA Corporation GT218 [GeForce G210M]', '10de', '0a74']]
        # Optimus test
        #deviceArray = [['Intel Corporation Haswell-ULT Integrated Graphics Controller', '8086', '0a16'], \
        #['NVIDIA Corporation GK107M [GeForce GT 750M]', '10de', '0fe4']]

        if deviceArray:
            optimus = False
            devices = []

            self.log.write("Device(s): {}".format(deviceArray), 'get_nvidia')

            # Check if nvidia is loaded
            # If it is: checkbox is selected
            loadedDrv = self.get_loaded_graphical_driver()
            self.log.write("Loaded graphical driver: {}".format(loadedDrv), 'get_nvidia')

            # Get the manufacturer's logo
            logo = join(self.mediaDir, 'images/nvidia.png')

            # Fill the hardware array
            for device in deviceArray:
                if device[1] == '8086':
                    optimus = True
                else:
                    devices.append(device)

            for device in devices:
                self.log.write("Nvidia device found: {}".format(device[0]), 'get_nvidia')
                optimusString = ""
                if optimus:
                    optimusString = "(Optimus) "
                shortDevice = "{0}{1}".format(optimusString, self.shorten_long_string(device[0], 50))

                # Check if the available driver is already loaded
                selected = False
                if optimus:
                    if loadedDrv == 'nvidia' or loadedDrv == 'intel':
                        bbversion = getPackageVersion("bumblebee-nvidia")
                        self.log.write("bumblebee-nvidia version: {}".format(bbversion), 'get_nvidia')
                        if bbversion != '':
                            selected = True
                elif loadedDrv == 'nvidia':
                    selected = True

                driver = ""
                if optimus:
                    driver = "bumblebee-nvidia"
                else:
                    nvidiaDetect = getoutput("nvidia-detect | grep nvidia- | tr -d ' '")
                    if nvidiaDetect:
                        driver = nvidiaDetect[0]

                self.log.write("Nvidia driver to use: {}".format(driver), 'get_nvidia')

                # Fill self.hardware
                if driver != "":
                    self.hardware.append([selected, logo, shortDevice, driver, device[1], device[2]])

    def get_broadcom(self):
        ## Hardware list (device ids)
        ## http://linuxwireless.org/en/users/Drivers/b43
        deviceIds = []
        deviceIds.append(['b43', '|4307|4311|4312|4315|4318|4319|4320|4321|4324|4331|4350|4353|4357|a8d6|a8d8|432c|'])
        deviceIds.append(['b43legacy', '|4301|4306|4325|'])
        deviceIds.append(['wldebian', '4313|4328|4329|432a|432b|432d|4358|4359|435a|a99d|'])
        deviceIds.append(['brcmdebian', '|576|4727|'])
        deviceIds.append(['unknown', '|4322|4360|4365|43b1|'])

        manufacturerId = '14e4'

        deviceArray = self.get_lspci_info(manufacturerId)

        # TESTING - Uncomment the following line for testing:
        #deviceArray = [['Broadcom Corporation BCM4312 802.11a/b/g', '4312']]

        if deviceArray:
            self.log.write("Device(s): {}".format(deviceArray), 'get_broadcom')
            # Check if broadcom is loaded
            # If it is: checkbox is selected
            loadedDrv = self.get_loaded_wireless_driver()
            self.log.write("Loaded wireless driver: {}".format(loadedDrv), 'get_broadcom')

            # Get the manufacturer's logo
            logo = join(self.mediaDir, 'images/broadcom.png')

            # Fill the hardware array
            for device in deviceArray:
                self.log.write("Broadcom device found: {}".format(device[0]), 'get_broadcom')
                shortDevice = self.shorten_long_string(device[0], 50)
                driver = ''
                for did in deviceIds:
                    #print(("{} in {}".format(device[2], did[1])))
                    if device[2] in did[1]:
                        driver = did[0]
                        break

                if driver != '':
                    if driver == 'unknown':
                        self.notSupported.append(device[0])
                        self.log.write("Broadcom device not supported: {}".format(device[0]), 'get_broadcom')
                    else:
                        self.log.write("Broadcom driver to use: {}".format(driver), 'get_broadcom')
                        # Check if the available driver is already loaded
                        selected = False
                        if loadedDrv == driver:
                            selected = True

                        # Fill self.hardware
                        self.hardware.append([selected, logo, shortDevice, driver, device[1], device[2]])

    def get_pae(self):
        machine = getoutput('uname -m')[0]
        release = getoutput('uname -r')[0]

        # TESTING - Uncomment the following line for testing:
        #release = '3.16.0-4-586

        self.log.write("PAE check: machine={} / release={}".format(machine, release), 'get_pae')

        if machine == 'i686':
            # Check if PAE is installed and running
            selected = False
            if 'pae' in release:
                self.paeBooted = True
                selected = True
            else:
                if getPackageVersion('linux-image-686-pae') != '':
                    selected = True

            # Get the logo
            logo = join(self.mediaDir, 'images/pae.png')

            # Fill self.hardware
            paeDescription = _("PAE capable system")
            self.hardware.append([selected, logo, paeDescription, '', 'pae', ''])

    def get_lspci_info(self, manufacturerId, filterString=''):
        deviceArray = []
        output = []

        # Check for Optimus
        if manufacturerId == '10de':
            output = getoutput("lspci -vnn | grep '\[030[02]\]'")
            # Testing
            #output = ['00:02.0 VGA compatible controller [0300]: Intel Corporation Haswell-ULT Integrated Graphics Controller [8086:0a16] (rev 09) (prog-if 00 [VGA controller])', \
            #          '01:00.0 3D controller [0302]: NVIDIA Corporation GK107M [GeForce GT 750M] [10de:0fe4] (rev a1)']

        # Optimus will return 2 devices
        # If there are less than 2 devices, do regular check
        if len(output) < 2:
            if filterString != '':
                filterString = " | grep {}".format(filterString)
            output = getoutput("lspci -nn -d {}:{}".format(manufacturerId, filterString))

        if output:
            self.log.write("lspci output = {}".format(output), 'get_lspci_info')

        for line in output:
            matchObj = re.search(':\W(.*)\W\[(.+):(.+)\]', line)
            if matchObj:
                deviceArray.append([matchObj.group(1), matchObj.group(2), matchObj.group(3)])
        return deviceArray

    def shorten_long_string(self, longString, charLen, breakOnWord=True):
        tmpArr = []
        if breakOnWord:
            stringArr = longString.split(' ')
            nrChrs = 0
            for s in stringArr:
                nrChrs += len(s) + 1
                if nrChrs < charLen:
                    tmpArr.append(s)
                else:
                    break
        else:
            if len(longString) > charLen:
                tmpArr.append("{}...".format(longString[0:charLen]))
            else:
                tmpArr.append(longString)
        return ' '.join(tmpArr)

    # Return graphics module used by X.org
    # TODO: is lsmod an alternative?
    def get_loaded_graphical_driver(self):
        # sort on the most recent X.org log
        module = ''
        logDir = '/var/log/'
        logPath = None
        logs = glob(os.path.join(logDir, 'Xorg.*.log*'))
        logs.sort()

        for logPath in logs:
            # Search for "depth" in each line and check the used module
            # Sometimes these logs are saved as binary: open as read-only binary
            # When opening as ascii, read() will throw error: "UnicodeDecodeError: 'utf-8' codec can't decode byte 0x80"
            f = open(logPath, 'rb')
            # ignore utf-8 binary read errors
            log = f.read(20).decode("utf-8", "ignore")
            f.close()
            matchObj = re.search('([a-zA-Z]*)\(\d+\):\s+depth.*framebuffer', log, flags=re.IGNORECASE)
            if matchObj:
                module = matchObj.group(1).lower()
                break

        return module

    # Return used wireless driver
    def get_loaded_wireless_driver(self):
        driver = ''
        logDir = '/var/log/'
        for logPath in glob(os.path.join(logDir, 'syslog*')):
            if driver is None and not 'gz' in logPath:
                # Open the log file
                lines = []
                with open(logPath) as f:
                    lines = list(f.read().splitlines())

                for line in reversed(lines):
                    # First check for Network Manager entry
                    # Search for wlan0 in each line and get the listed driver
                    matchObj = re.search('\(wlan\d\):.*driver:\s*\'([a-zA-Z0-9\-]*)', line, flags=re.IGNORECASE)
                    if matchObj:
                        driver = matchObj.group(1)
                        break
                    else:
                        # Wicd
                        # Search for ieee in each line and get the listed driver
                        matchObj = re.search('ieee.*implement', line, flags=re.IGNORECASE)
                        if matchObj:
                            driver = matchObj.group(0)
                            break
        return driver

    def show_message(self, cmdOutput, onlyOnError=False):
        try:
            msg = _("There was an error during the installation.\n"
                    "Please, run 'sudo apt-get -f install' in a terminal.\n"
                    "Visit our forum for support: http://forums.solydxk.com")
            self.log.write("Command output: {}".format(cmdOutput), 'show_message')
            if int(cmdOutput) != 255:
                if int(cmdOutput) > 1:
                    # There was an error
                    MessageDialogSafe(self.btnSave.get_label(), msg, Gtk.MessageType.ERROR, self.window).show()
                elif not onlyOnError:
                    msg = _("The software has been successfully installed.")
                    MessageDialogSafe(self.btnSave.get_label(), msg, Gtk.MessageType.INFO, self.window).show()
        except:
            MessageDialogSafe(self.btnSave.get_label(), cmdOutput, Gtk.MessageType.INFO, self.window).show()
