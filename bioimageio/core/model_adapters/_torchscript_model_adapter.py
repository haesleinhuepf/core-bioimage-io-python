import gc
import warnings
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np
import torch
import xarray as xr
from numpy.typing import NDArray

from bioimageio.spec.utils import download
from bioimageio.spec.model import v0_4, v0_5
from bioimageio.spec.model.v0_5 import RelativeFilePath

from ._model_adapter import ModelAdapter


class TorchscriptModelAdapter(ModelAdapter):
    def __init__(self, *, model_description: Union[v0_4.ModelDescr, v0_5.ModelDescr], devices: Optional[Sequence[str]] = None):
        super().__init__()
        if model_description.weights.torchscript is None:
            raise ValueError(f"No torchscript weights found for model {model_description.name}")

        src = model_description.weights.torchscript.source
        weight_path = download(
            src.get_absolute(model_description.root) if isinstance(src, RelativeFilePath) else src
        ).path
        if devices is None:
            self.devices = ["cuda" if torch.cuda.is_available() else "cpu"]
        else:
            self.devices = [torch.device(d) for d in devices]

        if len(self.devices) > 1:
            warnings.warn("Multiple devices for single torchscript model not yet implemented")

        self._model = torch.jit.load(weight_path)
        self._model.to(self.devices[0])
        self._internal_output_axes = [
            tuple(out.axes) if isinstance(out.axes, str) else tuple(a.id for a in out.axes)
            for out in model_description.outputs
        ]

    def forward(self, *batch: xr.DataArray) -> List[xr.DataArray]:
        with torch.no_grad():
            torch_tensor = [torch.from_numpy(b.data).to(self.devices[0]) for b in batch]
            _result: Union[  # pyright: ignore[reportUnknownVariableType]
                Tuple[NDArray[Any], ...], List[NDArray[Any]], NDArray[Any]
            ] = self._model.forward(*torch_tensor)
            if isinstance(_result, (tuple, list)):
                result: Sequence[NDArray[Any]] = _result
            else:
                result = [_result]

            result = [
                r.cpu().numpy() if not isinstance(r, np.ndarray) else r  # pyright: ignore[reportUnnecessaryIsInstance]
                for r in result
            ]

        assert len(result) == len(self._internal_output_axes)
        return [xr.DataArray(r, dims=axes) for r, axes in zip(result, self._internal_output_axes)]

    def unload(self) -> None:
        self._devices = None
        del self._model
        _ = gc.collect()  # deallocate memory
        torch.cuda.empty_cache()  # release reserved memory
