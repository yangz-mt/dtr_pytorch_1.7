# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##################################################################################################
#
# This is the main metrics validation files which validates the numbers reported by dcgm.
# It includes the following functionality
# 1.) Parses the command line arguments
# 2.) Gives the user to remove and install latest dcgm binaries
# 3.) Kills any existing instance of nv-hostengine and starts a new one.
# 4.) Starts dcgmproftestestor thread in its own instance - one each for number of GPU's the test
#     is run.
# 5.) Starts the dcgm thread reporting metrics numbers on all GPU's under test.
# 6.) Captures the memory usage before dcgmproftester is started, while the tests are running and
#     after the tests are done.
# 7.) Parses the log files generated by dcgm and dcgmproftester threads and compare the numbers
#     and determine pass and fail.
# 8.) Compares the memory before and after the tests for each GPU and determines a pass or fail.
# 9.) Outputs a Pass or fail at the end of the run.
#
##################################################################################################

import csv
import argparse
import time as tm
import os
import sys
import subprocess
from multiprocessing import Process
import util
import wget
import pandas

'''
Profiling Fields
'''
DCGM_FI_PROF_GR_ENGINE_ACTIVE = 1001
DCGM_FI_PROF_SM_ACTIVE = 1002
DCGM_FI_PROF_SM_OCCUPANCY = 1003
DCGM_FI_PROF_PIPE_TENSOR_ACTIVE = 1004
DCGM_FI_PROF_DRAM_ACTIVE = 1005
DCGM_FI_PROF_PIPE_FP64_ACTIVE = 1006
DCGM_FI_PROF_PIPE_FP32_ACTIVE = 1007
DCGM_FI_PROF_PIPE_FP16_ACTIVE = 1008
DCGM_FI_PROF_PCIE_TX_BYTES = 1009
DCGM_FI_PROF_PCIE_RX_BYTES = 1010

class RunValidateDcgm:
    def __init__(self):
        self.tar_dir = os.path.realpath(sys.path[0])
        self.prot_thread_gpu = []
        self.init_range = 0
        self.upper_range = 0
        self.lower_range = 0
        self.gpuCount = 0
        self.results = {}
        self.metrics_range_list = [DCGM_FI_PROF_PIPE_TENSOR_ACTIVE, DCGM_FI_PROF_PIPE_FP64_ACTIVE, \
                                   DCGM_FI_PROF_PIPE_FP32_ACTIVE, DCGM_FI_PROF_PIPE_FP16_ACTIVE]

        self.metrics_util_list = [DCGM_FI_PROF_GR_ENGINE_ACTIVE, DCGM_FI_PROF_SM_ACTIVE, \
                                  DCGM_FI_PROF_SM_OCCUPANCY, DCGM_FI_PROF_DRAM_ACTIVE, \
                                  DCGM_FI_PROF_PCIE_TX_BYTES, DCGM_FI_PROF_PCIE_RX_BYTES]

        self.metrics_range = {DCGM_FI_PROF_PIPE_TENSOR_ACTIVE:0.75, \
                            DCGM_FI_PROF_PIPE_FP64_ACTIVE:0.92, \
                            DCGM_FI_PROF_PIPE_FP32_ACTIVE:0.85, \
                            DCGM_FI_PROF_PIPE_FP16_ACTIVE:0.75}

        self.metrics_label = {DCGM_FI_PROF_GR_ENGINE_ACTIVE:'GRACT', \
                            DCGM_FI_PROF_SM_ACTIVE:'SMACT', \
                            DCGM_FI_PROF_SM_OCCUPANCY:'SMOCC', \
                            DCGM_FI_PROF_PIPE_TENSOR_ACTIVE:'TENSOR', \
                            DCGM_FI_PROF_DRAM_ACTIVE:'DRAMA', \
                            DCGM_FI_PROF_PIPE_FP64_ACTIVE:'FP64A', \
                            DCGM_FI_PROF_PIPE_FP32_ACTIVE:'FP32A', \
                            DCGM_FI_PROF_PIPE_FP16_ACTIVE:'FP16A', \
                            DCGM_FI_PROF_PCIE_TX_BYTES:'PCITX', \
                            DCGM_FI_PROF_PCIE_RX_BYTES:'PCIRX'}

    def getLatestDcgm(self):
        print("Getting the URL for latest dcgm package\n")
        baseurl = "http://cqa-fs01/dvsshare/dcgm/daily/r418_00/"
        cmd = 'wget -q -O - http://cqa-fs01/dvsshare/dcgm/daily/r418_00/ | grep -Eo \
                \\2019[0-9]{8} | tail -1'
        ret, folder_name = util.executeBashCmd(cmd, True)
        if "$folder_name" == "":
            print("Package index not found. Maybe the server is down?")
        dcgm_url = baseurl + folder_name + '/testing_dcgm/x86_64/testing_dcgm.tar.gz'
        deb_url = baseurl + folder_name + '/DEBS/datacenter-gpu-manager-dcp-nda-only_1.6.4_amd64.deb'
        return dcgm_url, deb_url

    #############################################################################################
    #
    # This function removes any dcgmi and dcgmproftester binaries
    # It also uninstalls any existing installation of datacenter gpu manager
    # Returns success in the end.
    #
    #############################################################################################
    def removeBinaries(self, prnt):
        #Remove existing installation files and binaries
        ret = util.executeBashCmd("sudo rm -rf testing_dcgm*", prnt)
        ret = util.executeBashCmd("sudo rm -rf datacenter-gpu-manager*.deb", prnt)
        ret = util.executeBashCmd("sudo rm -rf _out/", prnt)
        ret = util.executeBashCmd("sudo rm -rf /usr/bin/dcgmproftester", prnt)
        ret = util.executeBashCmd("sudo rm -rf *.txt", prnt)
        ret = util.executeBashCmd("sudo rm -rf *.csv", prnt)
        ret = util.executeBashCmd("sudo rm -rf *.pyc", prnt)

        #Uninstall dcgmi
        print("Removing existing installation of dcgmi")
        uninstall_cmd = "sudo dpkg --purge datacenter-gpu-manager-dcp-nda-only"
        ret = util.executeBashCmd(uninstall_cmd, prnt)
        if ret[0] != 0:
            print(("Error: Couldnt purge existing installation of \
                    datacenter-gpu-manager-dcp-nda-on, ret: ", ret))
        else:
            print("\nSUCCESS: No error on uninstall")

        uninstall_cmd = "sudo apt-get remove --purge datacenter-gpu-manager"
        ret = util.executeBashCmd(uninstall_cmd, prnt)
        if ret[0] != 0:
            print(("Error: Couldnt purge existing installation of datacenter-gpu-manager, ret: ", \
                    ret))
        else:
            print("\nSUCCESS: No error on uninstalling datacenter-gpu-manager")

        return 0

    #############################################################################################
    #
    # This function downloads the latest version of testing_dcgm.tar.gz and
    # datacenter-gpu-manager-dcp-nda-only_1.6.4_amd64.deb It is called only if user
    # specified "-d" option. Returns 0 for SUCCESS, -1 for any failure.
    #
    #############################################################################################
    def downloadInstallers(self, dcgm_url, deb_url):
        print(("&&&& INFO: Downloading latest testing_dcgm.tar.gz from", dcgm_url))
        #fileName = wget.download(self.tar_file, out=None)#, bar=None)  # no progress bar is shown
        fileName = wget.download(dcgm_url, out=None)#, bar=None)  # no progress bar is shown
        if not os.path.isfile(os.path.join(self.tar_dir, fileName)):
            print(("ERROR", "Unable to download specified packages: \n %s" % fileName))
            return -1
        else:
            print("SUCCESS: nDownload Success\n")

        print(("&&&& INFO: Downloading latest datacenter-gpu-manager-dcp-nda-only_1.6.4_amd64.deb \
                from", deb_url))
        self.deb_fileName = wget.download(deb_url, out=None)  # no progress bar is shown
        if not os.path.isfile(os.path.join(self.tar_dir, self.deb_fileName)):
            print(("ERROR", "Unable to download specified packages:\n %s" % self.deb_fileName))
            return -1

        print("\nSUCCESS: Download completed successfully")
        return 0

    def killNvHostEngine(self):
        print("\n&&&& INFO: Killing any existing nvhostengine instance")
        ret = util.executeBashCmd("sudo /usr/bin/nv-hostengine -t", True)

        print("\n&&&& INFO: Stopping dcgm service ")
        ret = util.executeBashCmd("sudo service dcgm stop", True)

    def startNvHostEngine(self):
        print("\n&&&& INFO: Killing any existing nvhostengine instance")
        ret = util.executeBashCmd("sudo /usr/bin/nv-hostengine -t", True)

        print("\n&&&& INFO: Stopping dcgm service ")
        ret = util.executeBashCmd("sudo service dcgm stop", True)

        print("\n&&&& INFO: Starting nvhostengine")
        ret = util.executeBashCmd("sudo /usr/bin/nv-hostengine", True)

        print("\n&&&& INFO: dcgmi discovery output")
        ret = util.executeBashCmd("sudo /usr/bin/dcgmi discovery -l", True)

        return ret

    def installDcgm(self):
        print("\n&&&& INFO: Installing latest version of datacenter-gpu-manager-dcp-nda-on")
        ret = util.executeBashCmd("sudo dpkg -i datacenter-gpu-manager-dcp-nda-only_1.6.4_amd64.deb", True)
        if ret[0] != 0:
            print(("ERROR: Couldnt install dcgmi, ret: ", ret))
        else:
            print("\nSUCCESS: Installed datacenter-gpu-manager-dcp-nda-on successfully")

        return ret[0]

    def installProfTester(self):
        ret = util.executeBashCmd("tar xvf testing_dcgm.tar.gz", True)
        if ret[0] == 0:
            ret = util.executeBashCmd("sudo cp _out/Linux_amd64_release/testing/apps/amd64/dcgmproftester /usr/bin/", True)
        else:
            print(("ERROR: Something went wrong in extracting testing_dcgm.tar.gz??, \
                    command returned: \n", ret))
        return ret[0]

    def _runDcgmLoadProfilingModule(self):
        print("\n&&&& INFO: Running dcgm to just to load profiling module once.")
        ret = util.executeBashCmd("timeout 3s /usr/bin/dcgmi dmon -e 1001 -i 0", False)

    #############################################################################################
    #
    # This function is called when dcgm thread is spawned to collect the metrics.
    # It executes dcgmi to collect metrics on all GPU's under test for a specified amount of time.
    # All information regarding how many GPU's to run the test on, time to run the tests, which
    # metrics to gather is specified by user. This thread is executing in parallel, control
    # immediately returns to the calling function.
    #
    #############################################################################################
    def _runDcgm(self, metrics, gpuid_list, time):
        print("\n&&&& INFO: Running dcgm to collect metrics on {0}".format(metrics))
        ret = util.executeBashCmd("echo {0} | timeout {0}s /usr/bin/dcgmi dmon -e {1} -i {2} 2>&1 | tee dcgmLogs_{3}.txt".format(time, metrics, gpuid_list, metrics), False)

    #############################################################################################
    #
    # This function is called when dcgmproftester thread is spawned to generate a workload.
    # It executes dcgmproftester to generate a workload on one GPU under test for a specified
    # amount of time. Information regarding time to run the tests, which metrics to gather,
    # and gpu to test this is specified by user. One thread is spawned for every GPU as generating
    # workload on multiple GPUs in the same instance is not supported by dcgmproftester yet.
    # This thread is executing in parallel, control immediately returns to the calling function.
    #
    #############################################################################################
    def _runProftester(self, gpuIndex, metric, time):
        metrics = str(metric)
        print("\n&&&& INFO: Running dcgmproftester to collect metrics on gpu {0}".format(gpuIndex))
        os.environ['CUDA_VISIBLE_DEVICES'] = str(gpuIndex)
        util.executeBashCmd("echo {0} | /usr/bin/dcgmproftester -d {0} -t {1} 2>&1 | tee dcgmLogsproftester_{2}_gpu{3}.txt".format(time, metrics, metrics, gpuIndex), False)

    #############################################################################################
    #
    # This function returns the column names in excel to read the data from, its based on metrics
    # information passed.
    #
    #############################################################################################
    def getColNames(self, metrics):
        colnames = []
        name = self.metrics_label[metrics]
        for i in range(0, self.gpuCount):
            colName = name + '_' + str(i)
            colnames.append(colName)
        return colnames, self.metrics_label[metrics]

    #############################################################################################
    #
    # This function defines an error margin based on the metrics.
    # For certain number of times, if dcgm reports numbers outside the error margin as compared to
    # what dcgmproftester is expecting, test will fail.
    #
    #############################################################################################
    def getMarginRange(self, metrics):
        if metrics == DCGM_FI_PROF_PCIE_RX_BYTES or metrics == DCGM_FI_PROF_PCIE_TX_BYTES:
            return 17.0, -10.0, 17.0
        elif metrics == DCGM_FI_PROF_GR_ENGINE_ACTIVE or metrics == DCGM_FI_PROF_SM_ACTIVE \
                or metrics == DCGM_FI_PROF_SM_OCCUPANCY or metrics == DCGM_FI_PROF_DRAM_ACTIVE:
            return 10.0, -10.0, 10.0

    #############################################################################################
    #
    # This function finds the first index in dcgm for which the value is within the error margin
    # when compared to dcgmproftester.This is for the metrics for PCIE metrics for which bandwidth
    # is reported instead of utilization. After we find this index, we continue to compare each
    # value and increment both dcgm and dcgmproftester values by 1.
    #
    #############################################################################################
    def findClosestIndexForBand(self, dcgm_list, prof_list, metrics):
        self.upper_range, self.lower_range, self.init_range = self.getMarginRange(metrics)
        print("\nPROFTESTER[9][1]: " + str(prof_list[9][1]))
        for dcgm_delay in range(6, len(dcgm_list)):
            err_mar = ((float(dcgm_list[dcgm_delay]) - float(prof_list[9][1]))*100/float(prof_list[9][1]))
            print('FINDING OUT - dcgm[' + str(dcgm_list[dcgm_delay])+'] proftester[' + str(prof_list[9][1])+'] error margin[' + str(err_mar)+ '] dcgm_delay[' + str(dcgm_delay)+ ']')
            if abs(err_mar) < self.init_range:
                err_mar_next = ((float(dcgm_list[dcgm_delay+1]) - float(prof_list[9][1]))*100/float(prof_list[9][1]))
                print('FINDING NXT--dcgm['+str(dcgm_list[dcgm_delay+1])+'] proftester[' +str(prof_list[9][1])+'] err margin['+str(err_mar_next)+'] dcgm_delay['+str(dcgm_delay)+ ']')
                if abs(err_mar_next) - self.init_range <= abs(err_mar) - self.init_range:
                    dcgm_delay = dcgm_delay + 1
                    print((abs(err_mar_next), self.init_range))
                    break
                else:
                    print((abs(err_mar), self.init_range))
                    break
            else:
                dcgm_delay = dcgm_delay + 1

        return dcgm_delay

    #############################################################################################
    #
    # This function finds the first index in dcgm for which the value is within the error margin
    # when compared to dcgmproftester. This is for the metrics for which utilization numbers are
    # reported. After we find this index, we continue to compare each value and increment both
    # dcgm and dcgmproftester values by 1.
    #
    #############################################################################################
    def getClosestIndexForUtil(self, dcgm_list, dcgm_init_val):
        i = 0
        #print dcgm_list
        for i in range(6, len(dcgm_list)):
            #print("COMPARING..." + str(dcgm_list[i]))
            if float(dcgm_list[i]) > dcgm_init_val:
                if abs(float(dcgm_list[i]) - dcgm_init_val) > abs(float(dcgm_list[i-1]) - dcgm_init_val):
                    i = i - 1
                    break
        return i

    def getClosestIndex(self, dcgm_list, prof_list, metrics, dcgm_init_val):
        i = 0
        if metrics == DCGM_FI_PROF_PCIE_TX_BYTES or metrics == DCGM_FI_PROF_PCIE_RX_BYTES:
            i = self.findClosestIndexForBand(dcgm_list, prof_list, metrics)
        elif metrics == DCGM_FI_PROF_GR_ENGINE_ACTIVE or metrics == DCGM_FI_PROF_SM_ACTIVE or metrics == DCGM_FI_PROF_SM_OCCUPANCY:
            i = self. getClosestIndexForUtil(dcgm_list, dcgm_init_val)

        return i


    #############################################################################################
    #
    # This function validates that the dcgm data gathered is within the expected range which is
    # pre-defined for some metrics.
    #
    #############################################################################################
    def validateAccuracyForRanges(self, dcgmCsvFile, gpu_index, metrics):
        ret = 0
        colnames, metric_label = self.getColNames(metrics)
        dcgm_col = metric_label + '_' + str(gpu_index)
        data = pandas.read_csv(dcgmCsvFile, skiprows=[0], names=colnames)
        dcgm_list = data[dcgm_col].tolist()
        for i in range(4, len(dcgm_list)-5):
            if dcgm_list[i] < self.metrics_range[metrics] or dcgm_list[i] > 1.0:
                print("FAILED: Entry num" + str(i) + ": " + str(dcgm_list[i]))
                ret = -1
            #else:
            #    print("Entry num" + str(i) + ": " + str(dcgm_list[i]))
        return ret

    #############################################################################################
    #
    # This function validates that the dcgm data gathered is within the expected error margin with
    # what is reported by dcgmproftester. If there are certain number of times that the data is out
    # of error margin, the test will fail.
    #
    #############################################################################################
    def validateAccuracyForUtilForUtil(self, dcgmCsvFile, dcgmProfTesterCsvFile, gpu_index, metrics):
        i = 0
        mismatches = 0
        spikes = 0
        dcgm_init_index = 5
        self.upper_range, self.lower_range, self.init_range = self.getMarginRange(metrics)
        with open(dcgmProfTesterCsvFile, 'r') as f2:
            c1 = csv.reader(f2)
            prof_list = list(c1)
            dcgm_init_val = float(prof_list[dcgm_init_index][1])
            print('\nValidating the accuracy of gpu' + str(gpu_index))

            colnames, metric_label = self.getColNames(metrics)
            dcgm_col = metric_label + '_' + str(gpu_index)
            data = pandas.read_csv(dcgmCsvFile, skiprows=[0], names=colnames)
            dcgm_list = data[dcgm_col].tolist()
            i = self.getClosestIndex(dcgm_list, prof_list, metrics, dcgm_init_val)
            dcgm_delay = 0
            try:
                # Ignoring the first few entries, starting from index 9
                len_dcgm = len(dcgm_list)
                tot_comp = len_dcgm-i
                while i < len_dcgm - (dcgm_delay):
                    err_mar = ((float(dcgm_list[i+dcgm_delay]) - float(prof_list[dcgm_init_index][1]))*100/float(prof_list[dcgm_init_index][1]))
                    err_mar_next_line = ((float(dcgm_list[i+dcgm_delay+1]) - float(prof_list[dcgm_init_index][1]))*100/float(prof_list[dcgm_init_index][1]))
                    if (err_mar > self.upper_range or err_mar < self.lower_range):
                        mismatches = mismatches + 1
                        print('1st check failed - dcgm['+str(dcgm_list[i+dcgm_delay])+'] dcgmproftester[' + str(prof_list[dcgm_init_index][1]) + '] Error margin[' + str(err_mar) + ']')
                        if (err_mar_next_line > self.upper_range or err_mar_next_line < self.lower_range):
                            spikes = spikes + 1
                            print('Failed 2nd time - dcgm[' + str(dcgm_list[i+dcgm_delay+1]) + '] dcgmproftester[' + str(prof_list[dcgm_init_index][1])+ '] Err Mar['+ str(err_mar_next_line))
                            i = i + 3
                            dcgm_init_index = dcgm_init_index + 3
                            continue
                        else:
                            print('SUCCESS with next entry - dcgm[' + str(dcgm_list[i+dcgm_delay+1]) + '] [dcgmproftester[' + str(prof_list[dcgm_init_index][1])+'] Err Mar[' + str(err_mar_next_line))
                            dcgm_delay = dcgm_delay + 1
                    else:
                        i = i+1
                        dcgm_init_index = dcgm_init_index + 1
            except (IndexError, ZeroDivisionError) as e:
                print("\nIndex or ZeroDiv Exception occured..Ignoring: ")
                i = i+1
                dcgm_init_index = dcgm_init_index + 1
            print('Spikes for gpu' + str(gpu_index) + ': ' + str(spikes))
            print('Total comparisons: ' + str(tot_comp) + ', Mismatches: ' + str(mismatches))
            failure_perc = float(mismatches * 100)/tot_comp
            print('Failure % for gpu' + str(gpu_index) + ': ' + str(failure_perc))
            if mismatches > 5:
                return -1

            return 0

    def validateAccuracy(self, dcgmCsvFile, dcgmProfTesterCsvFile, gpu_index, metrics):
        ret = 0
        if metrics in self.metrics_util_list:
            ret = self.validateAccuracyForUtilForUtil(dcgmCsvFile, dcgmProfTesterCsvFile, \
                    gpu_index, metrics)
        elif metrics in self.metrics_range:
            ret = self.validateAccuracyForRanges(dcgmCsvFile, gpu_index, metrics)
        else:
            print("Metrics: " + str(metrics) + "not supported\n")
        return ret

    ##############################################################################################
    #
    # This function gets the output of nvidia-smi for the calling function to get the memory
    # information
    #
    ##############################################################################################
    def getSmiOp(self):
        out = subprocess.Popen(['nvidia-smi'], \
                stdout=subprocess.PIPE, \
                stderr=subprocess.STDOUT)

        stdout, stderr = out.communicate()

        return stdout

    ##############################################################################################
    #
    # This function gets memory information out of nvidia-smi output
    #
    ##############################################################################################
    def getMemUsage(self, smi, gpu_list):
        mem_list = []
        smi_list = smi.split()
        indices = [i for i, s in enumerate(smi_list) if 'MiB' in s]
        for i in range(0, len(gpu_list)*2, 2):
            mem_list.append(smi_list[indices[i]])
        return mem_list

def main(cmdArgs):
    metrics = int(cmdArgs.metrics)
    gpuid_list = cmdArgs.gpuid_list
    time = int(cmdArgs.time)
    download_bin = cmdArgs.download_bin
    print(("Download_binaries: ", download_bin))
    if time < int(10):
        print('Modifying the time to 10s which is minimum\n')
        time = 10
    print(cmdArgs)
    ro = RunValidateDcgm()

    if download_bin:
        #Remove existing installation of dcgmi and dcgmproftestor
        ret = ro.removeBinaries(True)

        #download latest installers
        if ret == 0:
            dcgm_url, deb_url = ro.getLatestDcgm()
            ret = ro.downloadInstallers(dcgm_url, deb_url)
        else:
            print("ERROR: Some problem with removing binaries\n")
            print(ret)

        #Install latest dcgm
        if ret == 0:
            ret = ro.installDcgm()

        #Install latest dcgmproftester
        if ret == 0:
            ret = ro.installProfTester()
        else:
            print("Something went wrong installing dcgmproftester\n")

    #if(ret == 0):
    ret = ro.startNvHostEngine()

    print("\nSleeping for 2 seconds")
    tm.sleep(2)

    gpu_list = gpuid_list.split(",")
    ro.gpuCount = len(gpu_list)

    #spawn dcgmi thread to load the profiling module once.
    print("Start : %s" % tm.ctime())
    tm.sleep(2)
    dcgm_time = int(time) + 4
    dcgm_thread_load_profiling_module = Process(target=ro._runDcgmLoadProfilingModule, \
            name="dcgm_worker-%d" %metrics)
    dcgm_thread_load_profiling_module.start()

    #wait for the thread to finish
    dcgm_thread_load_profiling_module.join()

    smi_in_beg = ro.getSmiOp()
    mem_in_beg = ro.getMemUsage(smi_in_beg, gpu_list)
    #print ("In Beginning: \n" + str(smi_in_beg))

    #spawn dcgmproftester threads, one each for every GPU
    for i in range(0, len(gpu_list)):
        threadName = 'dcgmproftester_worker-' + str(gpu_list[i])
        print("\n&&&& RUNNING GPU_" + str(gpu_list[i]) + "_metric_validation_test")
        ro.prot_thread_gpu.append(Process(target=ro._runProftester, args=[gpu_list[i], metrics, \
                time], name=threadName))
        #print gpu_list, len(gpu_list)
        ro.prot_thread_gpu[i].start()

    #spawn dcgmi thread
    print("Start : %s" % tm.ctime())
    tm.sleep(2)
    dcgm_time = int(time) + 4
    dcgm_thread = Process(target=ro._runDcgm, args=[metrics, gpuid_list, dcgm_time], \
                  name="dcgm_worker-%s" %metrics)
    dcgm_thread.start()

    tm.sleep(time/2)

    smi_while_running = ro.getSmiOp()
    mem_in_between = ro.getMemUsage(smi_while_running, gpu_list)
    #print ("In Between: \n" + str(smi_while_running))

    #wait for the thread to finish
    dcgm_thread.join()

    for i in range(0, len(gpu_list)):
        ro.prot_thread_gpu[i].join()

    #Copy the dcgm data in csv file
    cmd = '{executable} python2 parse_dcgm_single_metric.py -f dcgmLogs_{0}.txt -m {1} -i {2}'.format(metrics, \
            metrics, gpuid_list, executable=sys.executable)
    ret = util.executeBashCmd(cmd, True)

    #Copy the dcgmproftester data in csv
    if metrics in ro.metrics_util_list:
        for i in range(0, len(gpu_list)):
            cmd = '{executable} parse_dcgmproftester_single_metric.py -f \
                  dcgmLogsproftester_{0}_gpu{1}.txt -m {2} -i {3}'.format(metrics, gpu_list[i], \
                  metrics, gpu_list[i], executable=sys.executable)
            ret = util.executeBashCmd(cmd, True)

    #Compare the results and determine pass and fail
    for i in range(0, len(gpu_list)):
        dcgm_file = 'dcgm_{0}.csv'.format(metrics)
        dcgmproftester_file = 'dcgmProfTester_{0}_gpu{1}.csv'.format(metrics, gpu_list[i])
        ret = ro.validateAccuracy(dcgm_file, dcgmproftester_file, int(gpu_list[i]), metrics)
        if ret == 0:
            print("\n&&&& PASSED GPU_" + str(gpu_list[i]) + "_metric_validation_test")
            ro.results[gpu_list[i]] = 'PASS'
        else:
            print("\n&&&& FAILED GPU_" + str(gpu_list[i]) + "_metric_validation_test")
            ro.results[gpu_list[i]] = 'FAIL'

    print("\n")
    #for i in range(0, len(gpu_list)):
        #print('Validation for GPU ' + str(gpu_list[i]) + ': ' + ro.results[gpu_list[i]])

    smi_at_end = ro.getSmiOp()
    mem_in_end = ro.getMemUsage(smi_at_end, gpu_list)


    print("\nMemory in Beg of test run of all GPU's under test: " + str(mem_in_beg))
    print("Memory in Between of test run of all GPU's under test: " + str(mem_in_between))
    print("Memory in end of test run of all GPU's under test: " + str(mem_in_end) + "\n")

    for i in range(0, len(gpu_list)):
        print("\n&&&& RUNNING GPU_" + str(gpu_list[i]) + "_memory_validation_test")
        val = int(mem_in_end[i][0:len(mem_in_end[i])-3])
        #print ("Val without string: ", val)
        if ((mem_in_beg[i] != mem_in_end[i]) or val > 156):
            print("\n&&&& FAILED GPU_" + str(gpu_list[i]) + "_memory_validation_test")
        else:
            print("\n&&&& PASSED GPU_" + str(gpu_list[i]) + "_memory_validation_test")

    if download_bin:
        ret = ro.removeBinaries(False)

    ret = ro.killNvHostEngine

    #Send out an email with the chart

def parseCommandLine():

    parser = argparse.ArgumentParser(description="Validation of dcgm metrics")
    parser.add_argument("-m", "--metrics", required=True, help="Metrics to be validated E.g. \
                        \"1009\", etc")
    parser.add_argument("-i", "--gpuid_list", required=False, default='0', help="comma separated \
                        gpu id list starting from 0, eg \"0,1,2\"")
    parser.add_argument("-t", "--time", required=True, help="time in seconds")
    parser.add_argument("-d", "--download_bin", action='store_true', required=False, \
                        default=False, help="If specified, download new binaries")

    args = parser.parse_args()

    return args

if __name__ == "__main__":

    # Parsing command line options
    CMDARGS = parseCommandLine()

    main(CMDARGS)