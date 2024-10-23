<h2>
  Towards Robust Blind Face Restoration with Codebook Lookup Transformer
</h2>

<a href="https://badge.fury.io/py/codeformer-pip"><img src="https://badge.fury.io/py/codeformer-pip.svg" alt="pypi version"></a>
<a href="https://huggingface.co/spaces/ArtGAN/Stable-Diffusion-ControlNet-WebUI"><img src="https://img.shields.io/badge/CodeFormer-Demo-orange" alt="HuggingFace Spaces"></a>

This repo is a PyTorch implementation of the paper [CodeFormer](https://arxiv.org/abs/2206.11253).

### Installation
```bash
pip install git+https://github.com/pravin-onelab/codeformer-4.0.git
```
### Usage
```python
from codeformer.app import inference_app

inference_app(
  image="3d array of image",
  background_enhance=True,
  face_upsample=True,
  upscale=2,
  codeformer_fidelity=0.5,
)
```


### License

This project is licensed under NTU S-Lab License 1.0. Redistribution and use should follow this license.
