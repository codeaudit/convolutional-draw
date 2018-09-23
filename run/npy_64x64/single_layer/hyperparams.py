import json
import os

from tabulate import tabulate


class HyperParameters():
    def __init__(self, snapshot_directory=None):
        self.image_size = (64, 64)
        self.chrz_size = (16, 16)  # needs to be 1/4 of image_size
        self.channels_r = 256
        self.channels_chz = 64
        self.inference_channels_map_x = 64
        self.inference_share_core = False
        self.inference_share_posterior = False
        self.generator_generation_steps = 12
        self.generator_share_core = False
        self.generator_share_prior = False
        self.layer_normalization_enabled = False
        self.pixel_sigma_i = 2.0
        self.pixel_sigma_f = 0.7
        self.pixel_n = 2 * 1e5

        if snapshot_directory is not None:
            json_path = os.path.join(snapshot_directory, self.filename)
            if os.path.exists(json_path) and os.path.isfile(json_path):
                with open(json_path, "r") as f:
                    print("loading", json_path)
                    obj = json.load(f)
                    for (key, value) in obj.items():
                        if isinstance(value, list):
                            value = tuple(value)
                        setattr(self, key, value)
            else:
                raise Exception

    @property
    def filename(self):
        return "hyperparams.json"

    def save(self, snapshot_directory):
        with open(os.path.join(snapshot_directory, self.filename), "w") as f:
            json.dump(self.__dict__, f, indent=4, sort_keys=True)

    def print(self):
        rows = []
        for key, value in self.__dict__.items():
            rows.append([key, value])
        print(tabulate(rows))
