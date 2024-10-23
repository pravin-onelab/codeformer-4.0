import copy
import re
from pathlib import Path

import cv2
import numpy as np
import torch

from codeformer.facelib.detection.yolov5face.models.yolo import Model
from codeformer.facelib.detection.yolov5face.utils.datasets import letterbox
from codeformer.facelib.detection.yolov5face.models.common import StemBlock, ShuffleV2Block, Conv, Concat, C3
from codeformer.facelib.detection.yolov5face.utils.general import (
    check_img_size,
    non_max_suppression_face,
    scale_coords,
    scale_coords_landmarks,
)

# IS_HIGH_VERSION = tuple(map(int, torch.__version__.split('+')[0].split('.')[:2])) >= (1, 9)
IS_HIGH_VERSION = [
    int(m)
    for m in list(
        re.findall(r"^([0-9]+)\.([0-9]+)\.([0-9]+)([^0-9][a-zA-Z0-9]*)?(\+git.*)?$", torch.__version__)[0][:3]
    )
] >= [1, 9, 0]


def isListempty(inList):
    if isinstance(inList, list):  # Is a list
        return all(map(isListempty, inList))
    return False  # Not a list


class YoloDetector:
    # this code block bypasses loading yolov5n.yaml
    def __init__(
        self,
        min_face=10,
        target_size=None,
        device="cuda",
    ):
        """
        min_face : minimal face size in pixels.
        target_size : target size of smaller image axis (choose lower for faster work). e.g. 480, 720, 1080.
                      None for original resolution.
        """
        self._class_path = Path(__file__).parent.absolute()
        self.target_size = target_size
        self.min_face = min_face
        self.device = device

        # Initialize with predefined YAML configuration
        self.yaml_config = {
            'nc': 1,  # Number of classes
            'depth_multiple': 1.0,
            'width_multiple': 1.0,
            'anchors': [
                [4, 5, 8, 10, 13, 16],
                [23, 29, 43, 55, 73, 105],
                [146, 217, 231, 300, 335, 433]
            ],
            'backbone': [
                [-1, 1, StemBlock, [32, 3, 2]],    # 0-P2/4
                [-1, 1, ShuffleV2Block, [128, 2]], # 1-P3/8
                [-1, 3, ShuffleV2Block, [128, 1]], # 2
                [-1, 1, ShuffleV2Block, [256, 2]], # 3-P4/16
                [-1, 7, ShuffleV2Block, [256, 1]], # 4
                [-1, 1, ShuffleV2Block, [512, 2]], # 5-P5/32
                [-1, 3, ShuffleV2Block, [512, 1]], # 6
            ],
            'head': [
                [-1, 1, Conv, [128, 1, 1]],
                [-1, 1, nn.Upsample, [None, 2, 'nearest']],
                [[-1, 4], 1, Concat, [1]],  # cat backbone P4
                [-1, 1, C3, [128, False]],  # 10

                [-1, 1, Conv, [128, 1, 1]],
                [-1, 1, nn.Upsample, [None, 2, 'nearest']],
                [[-1, 2], 1, Concat, [1]],  # cat backbone P3
                [-1, 1, C3, [128, False]],  # 14 (P3/8-small)

                [-1, 1, Conv, [128, 3, 2]],
                [[-1, 11], 1, Concat, [1]],  # cat head P4
                [-1, 1, C3, [128, False]],  # 17 (P4/16-medium)

                [-1, 1, Conv, [128, 3, 2]],
                [[-1, 7], 1, Concat, [1]],  # cat head P5
                [-1, 1, C3, [128, False]],  # 20 (P5/32-large)

                [[14, 17, 20], 1, Detect, [self.yaml_config['nc'], self.yaml_config['anchors']]],  # Detect(P3, P4, P5)
            ]
        }

        # Initialize the Model with the predefined configuration
        self.detector = Model(config=self.yaml_config, ch=3)

    # # This is original code block
    # def __init__(
    #     self,
    #     config_name,
    #     min_face=10,
    #     target_size=None,
    #     device="cuda",
    # ):
    #     """
    #     config_name: name of .yaml config with network configuration from models/ folder.
    #     min_face : minimal face size in pixels.
    #     target_size : target size of smaller image axis (choose lower for faster work). e.g. 480, 720, 1080.
    #                 None for original resolution.
    #     """
    #     self._class_path = Path(__file__).parent.absolute()
    #     self.target_size = target_size
    #     self.min_face = min_face
    #     self.detector = Model(cfg=config_name)
    #     self.device = device

    def _preprocess(self, imgs):
        """
        Preprocessing image before passing through the network. Resize and conversion to torch tensor.
        """
        pp_imgs = []
        for img in imgs:
            h0, w0 = img.shape[:2]  # orig hw
            if self.target_size:
                r = self.target_size / min(h0, w0)  # resize image to img_size
                if r < 1:
                    img = cv2.resize(img, (int(w0 * r), int(h0 * r)), interpolation=cv2.INTER_LINEAR)

            imgsz = check_img_size(max(img.shape[:2]), s=self.detector.stride.max())  # check img_size
            img = letterbox(img, new_shape=imgsz)[0]
            pp_imgs.append(img)
        pp_imgs = np.array(pp_imgs)
        pp_imgs = pp_imgs.transpose(0, 3, 1, 2)
        pp_imgs = torch.from_numpy(pp_imgs).to(self.device)
        pp_imgs = pp_imgs.float()  # uint8 to fp16/32
        return pp_imgs / 255.0  # 0 - 255 to 0.0 - 1.0

    def _postprocess(self, imgs, origimgs, pred, conf_thres, iou_thres):
        """
        Postprocessing of raw pytorch model output.
        Returns:
            bboxes: list of arrays with 4 coordinates of bounding boxes with format x1,y1,x2,y2.
            points: list of arrays with coordinates of 5 facial keypoints (eyes, nose, lips corners).
        """
        bboxes = [[] for _ in range(len(origimgs))]
        landmarks = [[] for _ in range(len(origimgs))]

        pred = non_max_suppression_face(pred, conf_thres, iou_thres)

        for image_id, origimg in enumerate(origimgs):
            img_shape = origimg.shape
            image_height, image_width = img_shape[:2]
            gn = torch.tensor(img_shape)[[1, 0, 1, 0]]  # normalization gain whwh
            gn_lks = torch.tensor(img_shape)[[1, 0, 1, 0, 1, 0, 1, 0, 1, 0]]  # normalization gain landmarks
            det = pred[image_id].cpu()
            scale_coords(imgs[image_id].shape[1:], det[:, :4], img_shape).round()
            scale_coords_landmarks(imgs[image_id].shape[1:], det[:, 5:15], img_shape).round()

            for j in range(det.size()[0]):
                box = (det[j, :4].view(1, 4) / gn).view(-1).tolist()
                box = list(
                    map(int, [box[0] * image_width, box[1] * image_height, box[2] * image_width, box[3] * image_height])
                )
                if box[3] - box[1] < self.min_face:
                    continue
                lm = (det[j, 5:15].view(1, 10) / gn_lks).view(-1).tolist()
                lm = list(map(int, [i * image_width if j % 2 == 0 else i * image_height for j, i in enumerate(lm)]))
                lm = [lm[i : i + 2] for i in range(0, len(lm), 2)]
                bboxes[image_id].append(box)
                landmarks[image_id].append(lm)
        return bboxes, landmarks

    def detect_faces(self, imgs, conf_thres=0.7, iou_thres=0.5):
        """
        Get bbox coordinates and keypoints of faces on original image.
        Params:
            imgs: image or list of images to detect faces on with BGR order (convert to RGB order for inference)
            conf_thres: confidence threshold for each prediction
            iou_thres: threshold for NMS (filter of intersecting bboxes)
        Returns:
            bboxes: list of arrays with 4 coordinates of bounding boxes with format x1,y1,x2,y2.
            points: list of arrays with coordinates of 5 facial keypoints (eyes, nose, lips corners).
        """
        # Pass input images through face detector
        images = imgs if isinstance(imgs, list) else [imgs]
        images = [cv2.cvtColor(img, cv2.COLOR_BGR2RGB) for img in images]
        origimgs = copy.deepcopy(images)

        images = self._preprocess(images)

        if IS_HIGH_VERSION:
            with torch.inference_mode():  # for pytorch>=1.9
                pred = self.detector(images)[0]
        else:
            with torch.no_grad():  # for pytorch<1.9
                pred = self.detector(images)[0]

        bboxes, points = self._postprocess(images, origimgs, pred, conf_thres, iou_thres)

        # return bboxes, points
        if not isListempty(points):
            bboxes = np.array(bboxes).reshape(-1, 4)
            points = np.array(points).reshape(-1, 10)
            padding = bboxes[:, 0].reshape(-1, 1)
            return np.concatenate((bboxes, padding, points), axis=1)
        else:
            return None

    def __call__(self, *args):
        return self.predict(*args)
