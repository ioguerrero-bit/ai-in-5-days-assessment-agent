"""Compatibility shim enabling stdlib-only test execution when Pydantic is absent.

In normal installations (`pip install -e .`), genuine Pydantic v2 is imported
directly with zero shim involvement.
"""

from __future__ import annotations

from dataclasses import MISSING
from typing import Any, get_args, get_origin, get_type_hints

try:
    from pydantic import (  # type: ignore[import-untyped]
        BaseModel,
        ConfigDict,
        Field,
        ValidationError,
        model_validator,
    )

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

    class ValidationError(ValueError):
        """Fallback validation error mirroring Pydantic ValidationError."""

    class ConfigDict(dict):
        """Fallback ConfigDict."""

    class _FieldInfo:
        def __init__(
            self,
            default: Any = MISSING,
            *,
            default_factory: Any = None,
            description: str = "",
            ge: int | float | None = None,
            le: int | float | None = None,
            min_length: int | None = None,
            max_length: int | None = None,
        ) -> None:
            self.default = default
            self.default_factory = default_factory
            self.description = description
            self.ge = ge
            self.le = le
            self.min_length = min_length
            self.max_length = max_length

    def Field(  # noqa: N802
        default: Any = MISSING,
        *,
        default_factory: Any = None,
        description: str = "",
        ge: int | float | None = None,
        le: int | float | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
    ) -> Any:
        return _FieldInfo(
            default=default,
            default_factory=default_factory,
            description=description,
            ge=ge,
            le=le,
            min_length=min_length,
            max_length=max_length,
        )

    def model_validator(*, mode: str = "after"):
        def decorator(fn):
            fn._is_model_validator = True  # type: ignore[attr-defined]
            fn._validator_mode = mode  # type: ignore[attr-defined]
            return fn

        return decorator

    class BaseModel:  # type: ignore[no-redef]
        """Minimal strict schema validator compatible with Pydantic v2 call sites."""

        model_config: dict[str, Any] = {}

        def __init__(self, **data: Any) -> None:
            annotations = self._all_annotations()
            extra_mode = self.model_config.get("extra", "ignore")
            if extra_mode == "forbid":
                unknown = set(data.keys()) - set(annotations.keys())
                if unknown:
                    raise ValidationError(f"Unexpected fields: {sorted(unknown)}")

            for field_name, field_type in annotations.items():
                field_meta = self._get_field_info(field_name)
                if field_name in data:
                    raw_val = data[field_name]
                elif isinstance(field_meta, _FieldInfo):
                    if field_meta.default_factory is not None:
                        raw_val = field_meta.default_factory()
                    elif field_meta.default is not MISSING:
                        raw_val = field_meta.default
                    else:
                        raise ValidationError(f"Missing required field: '{field_name}'")
                elif field_meta is not MISSING:
                    raw_val = field_meta
                else:
                    raise ValidationError(f"Missing required field: '{field_name}'")

                val = self._coerce_and_validate(field_name, raw_val, field_type, field_meta)
                setattr(self, field_name, val)

            for attr_name in dir(self.__class__):
                attr = getattr(self.__class__, attr_name, None)
                if getattr(attr, "_is_model_validator", False):
                    attr(self)

        @classmethod
        def _all_annotations(cls) -> dict[str, Any]:
            merged: dict[str, Any] = {}
            for base in reversed(cls.__mro__):
                if base is object:
                    continue
                try:
                    merged.update(get_type_hints(base))
                except Exception:  # noqa: BLE001
                    merged.update(getattr(base, "__annotations__", {}))
            merged.pop("model_config", None)
            return merged

        @classmethod
        def _get_field_info(cls, name: str) -> Any:
            for base in cls.__mro__:
                if name in base.__dict__:
                    return base.__dict__[name]
            return MISSING

        @classmethod
        def _coerce_and_validate(
            cls, name: str, val: Any, field_type: Any, meta: Any
        ) -> Any:
            origin = get_origin(field_type)
            args = get_args(field_type)

            if origin is list and isinstance(val, list) and args:
                inner = args[0]
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    val = [
                        item if isinstance(item, inner) else inner(**item)
                        for item in val
                    ]
            elif isinstance(field_type, type) and issubclass(field_type, BaseModel):
                if isinstance(val, dict):
                    val = field_type(**val)
                elif not isinstance(val, field_type):
                    raise ValidationError(f"Field '{name}' must be {field_type.__name__}")
            elif str(origin).endswith("Literal") and args:
                if val not in args:
                    raise ValidationError(f"Field '{name}' must be one of {args}, got {val!r}")

            if isinstance(meta, _FieldInfo):
                if meta.ge is not None and isinstance(val, (int, float)) and val < meta.ge:
                    raise ValidationError(f"Field '{name}' must be >= {meta.ge}, got {val}")
                if meta.le is not None and isinstance(val, (int, float)) and val > meta.le:
                    raise ValidationError(f"Field '{name}' must be <= {meta.le}, got {val}")
                if meta.min_length is not None and hasattr(val, "__len__") and len(val) < meta.min_length:
                    raise ValidationError(
                        f"Field '{name}' length must be >= {meta.min_length}"
                    )
                if meta.max_length is not None and hasattr(val, "__len__") and len(val) > meta.max_length:
                    raise ValidationError(
                        f"Field '{name}' length must be <= {meta.max_length}"
                    )
            return val

        def model_dump(self) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for name in self._all_annotations():
                val = getattr(self, name)
                if isinstance(val, BaseModel):
                    result[name] = val.model_dump()
                elif isinstance(val, list):
                    result[name] = [
                        item.model_dump() if isinstance(item, BaseModel) else item
                        for item in val
                    ]
                else:
                    result[name] = val
            return result

        @classmethod
        def model_validate(cls, obj: Any):
            if isinstance(obj, cls):
                return obj
            if isinstance(obj, dict):
                return cls(**obj)
            raise ValidationError(f"Cannot validate {type(obj).__name__} as {cls.__name__}")

        @classmethod
        def model_json_schema(cls) -> dict[str, Any]:
            props: dict[str, Any] = {}
            required: list[str] = []
            for name, ftype in cls._all_annotations().items():
                meta = cls._get_field_info(name)
                entry: dict[str, Any] = {"type": str(ftype)}
                if isinstance(meta, _FieldInfo):
                    if meta.description:
                        entry["description"] = meta.description
                    if meta.ge is not None:
                        entry["minimum"] = meta.ge
                    if meta.le is not None:
                        entry["maximum"] = meta.le
                    if meta.default is MISSING and meta.default_factory is None:
                        required.append(name)
                elif meta is MISSING:
                    required.append(name)
                props[name] = entry
            return {
                "title": cls.__name__,
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": cls.model_config.get("extra", "ignore") != "forbid",
            }
