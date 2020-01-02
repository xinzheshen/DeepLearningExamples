# *****************************************************************************
#  Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#      * Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#      * Redistributions in binary form must reproduce the above copyright
#        notice, this list of conditions and the following disclaimer in the
#        documentation and/or other materials provided with the distribution.
#      * Neither the name of the NVIDIA CORPORATION nor the
#        names of its contributors may be used to endorse or promote products
#        derived from this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
#  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
#  WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL NVIDIA CORPORATION BE LIABLE FOR ANY
#  DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
#  (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
#  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
#  ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# *****************************************************************************

from tacotron2.text import text_to_sequence
import models
import torch
import argparse
import numpy as np
from scipy.io.wavfile import write

import sys

import time
from dllogger.logger import LOGGER
import dllogger.logger as dllg
from dllogger.autologging import log_hardware, log_args

from apex import amp

def parse_args(parser):
    """
    Parse commandline arguments.
    """
    parser.add_argument('--tacotron2', type=str,
                        help='full path to the Tacotron2 model checkpoint file')
    parser.add_argument('--waveglow', type=str,
                        help='full path to the WaveGlow model checkpoint file')
    parser.add_argument('-s', '--sigma-infer', default=0.6, type=float)
    parser.add_argument('-sr', '--sampling-rate', default=22050, type=int,
                        help='Sampling rate')
    parser.add_argument('--amp-run', action='store_true',
                        help='inference with AMP')
    parser.add_argument('--log-file', type=str, default='nvlog.json',
                        help='Filename for logging')
    parser.add_argument('--stft-hop-length', type=int, default=256,
                        help='STFT hop length for estimating audio length from mel size')
    parser.add_argument('--num-iters', type=int, default=10,
                        help='Number of iterations')
    parser.add_argument('-il', '--input-length', type=int, default=64,
                        help='Input length')
    parser.add_argument('-bs', '--batch-size', type=int, default=1,
                        help='Batch size')


    return parser


def checkpoint_from_distributed(state_dict):
    """
    Checks whether checkpoint was generated by DistributedDataParallel. DDP
    wraps model in additional "module.", it needs to be unwrapped for single
    GPU inference.
    :param state_dict: model's state dict
    """
    ret = False
    for key, _ in state_dict.items():
        if key.find('module.') != -1:
            ret = True
            break
    return ret


def unwrap_distributed(state_dict):
    """
    Unwraps model from DistributedDataParallel.
    DDP wraps model in additional "module.", it needs to be removed for single
    GPU inference.
    :param state_dict: model's state dict
    """
    new_state_dict = {}
    for key, value in state_dict.items():
        new_key = key.replace('module.', '')
        new_state_dict[new_key] = value
    return new_state_dict


def load_and_setup_model(model_name, parser, checkpoint, amp_run, to_cuda=True):
    model_parser = models.parse_model_args(model_name, parser, add_help=False)
    model_args, _ = model_parser.parse_known_args()

    model_config = models.get_model_config(model_name, model_args)
    model = models.get_model(model_name, model_config, to_cuda=to_cuda)

    if checkpoint is not None:
        if to_cuda:
            state_dict = torch.load(checkpoint)['state_dict']
        else:
            state_dict = torch.load(checkpoint,map_location='cpu')['state_dict']
        if checkpoint_from_distributed(state_dict):
            state_dict = unwrap_distributed(state_dict)

        model.load_state_dict(state_dict)

    if model_name == "WaveGlow":
        model = model.remove_weightnorm(model)

    model.eval()

    if amp_run:
        model, _ = amp.initialize(model, [], opt_level="O3")

    return model


# taken from tacotron2/data_function.py:TextMelCollate.__call__
def pad_sequences(batch):
    # Right zero-pad all one-hot text sequences to max input length
    input_lengths, ids_sorted_decreasing = torch.sort(
        torch.LongTensor([len(x) for x in batch]),
        dim=0, descending=True)
    max_input_len = input_lengths[0]

    text_padded = torch.LongTensor(len(batch), max_input_len)
    text_padded.zero_()
    for i in range(len(ids_sorted_decreasing)):
        text = batch[ids_sorted_decreasing[i]]
        text_padded[i, :text.size(0)] = text

    return text_padded, input_lengths


def prepare_input_sequence(texts):

    d = []
    for i,text in enumerate(texts):
        d.append(torch.IntTensor(text_to_sequence(text, ['transliteration_cleaners'])[:]))

    text_padded, input_lengths = pad_sequences(d)
    if torch.cuda.is_available():
        text_padded = torch.autograd.Variable(text_padded).cuda().long()
        input_lengths = torch.autograd.Variable(input_lengths).cuda().long()
    else:
        text_padded = torch.autograd.Variable(text_padded).long()
        input_lengths = torch.autograd.Variable(input_lengths).long()

    return text_padded, input_lengths

class MeasureTime():
    def __init__(self, measurements, key):
        self.measurements = measurements
        self.key = key

    def __enter__(self):
        torch.cuda.synchronize()
        self.t0 = time.perf_counter()

    def __exit__(self, exc_type, exc_value, exc_traceback):
        torch.cuda.synchronize()
        self.measurements[self.key] = time.perf_counter() - self.t0


def main():
    """
    Launches text to speech (inference).
    Inference is executed on a single GPU.
    """
    parser = argparse.ArgumentParser(
        description='PyTorch Tacotron 2 Inference')
    parser = parse_args(parser)
    args, unknown_args = parser.parse_known_args()

    LOGGER.set_model_name("Tacotron2_PyT")
    LOGGER.set_backends([
        dllg.JsonBackend(log_file=args.log_file,
                         logging_scope=dllg.TRAIN_ITER_SCOPE, iteration_interval=1)
    ])
    LOGGER.register_metric("pre_processing", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("tacotron2_items_per_sec", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("tacotron2_latency", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("waveglow_items_per_sec", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("waveglow_latency", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("latency", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("type_conversion", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("storage", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("data_transfer", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("num_mels_per_audio", metric_scope=dllg.TRAIN_ITER_SCOPE)
    LOGGER.register_metric("throughput", metric_scope=dllg.TRAIN_ITER_SCOPE)

    measurements_all = {"pre_processing": [],
                        "tacotron2_latency": [],
                        "waveglow_latency": [],
                        "latency": [],
                        "type_conversion": [],
                        "data_transfer": [],
                        "storage": [],
                        "tacotron2_items_per_sec": [],
                        "waveglow_items_per_sec": [],
                        "num_mels_per_audio": [],
                        "throughput": []}

    log_hardware()
    log_args(args)

    print("args:", args, unknown_args)

    tacotron2 = load_and_setup_model('Tacotron2', parser, args.tacotron2, args.amp_run)
    waveglow = load_and_setup_model('WaveGlow', parser, args.waveglow, args.amp_run)

    # texts = ["The forms of printed letters should be beautiful, and that their arrangement on the page should be reasonable and a help to the shapeliness of the letters themselves. The forms of printed letters should be beautiful, and that their arrangement on the page should be reasonable and a help to the shapeliness of the letters themselves."]
    # texts = ["chang2 cheng2 shi4 gu3 dai4 zhong1 guo2 zai4 bu4 tong2 shi2 qi1 wei4 di3 yu4 sai4 bei3 you2 mu4 bu4 luo4 qin1 xi2 er2 xiu1 zhu4 de5 gui1 mo2 hao4 da4 de5 jun1 shi4 gong1 cheng2"]
    texts = ["chang2 cheng2 shi4 gu3 dai4 zhong1 guo2"]
    texts = [texts[0][:args.input_length]]
    texts = texts*args.batch_size

    warmup_iters = 3

    for iter in range(args.num_iters):

        if iter >= warmup_iters:
            LOGGER.iteration_start()

        measurements = {}

        with MeasureTime(measurements, "pre_processing"):
            sequences_padded, input_lengths = prepare_input_sequence(texts)

        with torch.no_grad():
            with MeasureTime(measurements, "latency"):
                with MeasureTime(measurements, "tacotron2_latency"):
                    mel, mel_lengths = tacotron2.infer(sequences_padded, input_lengths)

                with MeasureTime(measurements, "waveglow_latency"):
                    audios = waveglow.infer(mel, sigma=args.sigma_infer)

        num_mels = mel.size(0)*mel.size(2)
        num_samples = audios.size(0)*audios.size(1)

        with MeasureTime(measurements, "type_conversion"):
            audios = audios.float()

        with MeasureTime(measurements, "data_transfer"):
            audios = audios.cpu()

        with MeasureTime(measurements, "storage"):
            audios = audios.numpy()
            for i, audio in enumerate(audios):
                audio_path = "audio_"+str(i)+".wav"
                write(audio_path, args.sampling_rate,
                      audio[:mel_lengths[i]*args.stft_hop_length])

        measurements['tacotron2_items_per_sec'] = num_mels/measurements['tacotron2_latency']
        measurements['waveglow_items_per_sec'] = num_samples/measurements['waveglow_latency']
        measurements['num_mels_per_audio'] = mel.size(2)
        measurements['throughput'] = num_samples/measurements['latency']

        if iter >= warmup_iters:
            for k,v in measurements.items():
                measurements_all[k].append(v)
                LOGGER.log(key=k, value=v)

            LOGGER.iteration_stop()

    LOGGER.finish()

    print(np.mean(measurements_all['latency'][1:]),
          np.mean(measurements_all['throughput'][1:]),
          np.mean(measurements_all['pre_processing'][1:]),
          np.mean(measurements_all['type_conversion'][1:])+
          np.mean(measurements_all['storage'][1:])+
          np.mean(measurements_all['data_transfer'][1:]),
          np.mean(measurements_all['num_mels_per_audio'][1:]))

    throughput = measurements_all['throughput']
    preprocessing = measurements_all['pre_processing']
    type_conversion = measurements_all['type_conversion']
    storage = measurements_all['storage']
    data_transfer = measurements_all['data_transfer']
    postprocessing = [sum(p) for p in zip(type_conversion,storage,data_transfer)]
    latency = measurements_all['latency']
    num_mels_per_audio = measurements_all['num_mels_per_audio']

    latency.sort()

    cf_50 = max(latency[:int(len(latency)*0.50)])
    cf_90 = max(latency[:int(len(latency)*0.90)])
    cf_95 = max(latency[:int(len(latency)*0.95)])
    cf_99 = max(latency[:int(len(latency)*0.99)])
    cf_100 = max(latency[:int(len(latency)*1.0)])

    print("Throughput average (samples/sec) = {:.4f}".format(np.mean(throughput)))
    print("Preprocessing average (seconds) = {:.4f}".format(np.mean(preprocessing)))
    print("Postprocessing average (seconds) = {:.4f}".format(np.mean(postprocessing)))
    print("Number of mels per audio average = {}".format(np.mean(num_mels_per_audio)))
    print("Latency average (seconds) = {:.4f}".format(np.mean(latency)))
    print("Latency std (seconds) = {:.4f}".format(np.std(latency)))
    print("Latency cl 50 (seconds) = {:.4f}".format(cf_50))
    print("Latency cl 90 (seconds) = {:.4f}".format(cf_90))
    print("Latency cl 95 (seconds) = {:.4f}".format(cf_95))
    print("Latency cl 99 (seconds) = {:.4f}".format(cf_99))
    print("Latency cl 100 (seconds) = {:.4f}".format(cf_100))

if __name__ == '__main__':
    main()
