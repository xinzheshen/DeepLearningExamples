import os
import ffmpeg

# input_folder = '/home/shenxz/dataset/BZNSYP/wavs/000002.wav'
# output_folder = '/home/shenxz/dataset/BZNSYP/wavs_16k/000002_16.wav'
# ffmpeg.input(input_folder).output(os.path.join(output_folder), ar=16000).run()
# input_folder = '/home/shenxz/dataset/BZNSYP/wavs'
# output_folder = '/home/shenxz/dataset/BZNSYP/wavs_22'
#
# not_processed_file = []
# for file in os.listdir(input_folder):
#     if file.endswith('.wav'):
#         try:
#             ffmpeg.input(os.path.join(input_folder, file)).output(os.path.join(output_folder, file), ar=22050).run()
#         except Exception as e:
#             not_processed_file.append(file)
#             print(e)
#
# print(not_processed_file)

import torch
c = 3
print(torch.FloatTensor(c, c).normal_())
W = torch.qr(torch.FloatTensor(c, c).normal_())
print(W)
print(torch.det(W))
print("end")