import re

# file = "/home/shenxz/dataset/BZNSYP/ProsodyLabeling/000001-010000.txt"
# output = "/home/shenxz/dataset/BZNSYP/metadata.csv"
# file = "/home/shenxz/dataset/BZNSYP/filelist.txt"
# output = "/home/shenxz/dataset/BZNSYP/bznsyp_audio_text_train_filelist.txt"
# file = "/home/shenxz/dataset/BZNSYP/bznsyp_audio_text_val_filelist.txt"
# output = "/home/shenxz/dataset/BZNSYP/bznsyp_mel_text_val_filelist.txt"
file = "/home/shenxz/dataset/BZNSYP/train.txt"
output = "/home/shenxz/dataset/BZNSYP/train0.txt"


def get_line():
    with open(file) as f:
        for line in f:
            yield line


def write_line(line_gen):
    # count = 1
    # num = ""
    # text = ""
    # with open(output, 'w') as f:
    #     for line in line_gen:
    #         if line and line[0].isnumeric():
    #             num = line.split('\t')[0]
    #             num = "BZNSYP/wavs/" + num + '.wav'
    #         else:
    #             text = line.lstrip()
    #
    #         if count % 2 == 0:
    #             result = num + "|" + text
    #             f.write(result)
    #         count += 1

    count = 0
    with open(output, "w") as f:
        for line in line_gen:
            count += 1
            if count % 49 != 0 and count % 100 != 0:
                f.write(line)

def write_mel_path(line_gen):
    with open(output, "w") as f:
        for line in line_gen:
            line = line.replace("wavs", "mels")
            line = line.replace(".wav", ".pt")
            f.write(line)

def write_train_content(line_gen):
    with open(output, "w") as f:
        for line in line_gen:
            # line = re.sub(".*/.*/", '', line)
            line = line.replace(".wav", ".pt")
            f.write(line)

if __name__ == '__main__':
    line_gen = get_line()
    # write_line(line_gen)
    write_train_content(line_gen)