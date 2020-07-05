# Copyright (C) 2020 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import os
import datetime
import dateutil.parser
import json
from shutil import move
from tempfile import mkstemp

def deserialize_disk_json(obj):
    if "__isodt__" in obj:
        try:
            return dateutil.parser.parse(obj["__isodt__"])
        except (ValueError, OverflowError) as e:
            raise json.decoder.JSONDecodeError(
                "Failed to decode ISO format datetime: {e}"
            ).with_traceback(e.__traceback__)
    return obj

def serialize_disk_json(obj):
    if isinstance(obj, bytes):
        return obj.decode()
    if isinstance(obj, datetime.datetime):
        return {"__isodt__": obj.isoformat()}
    return obj

def serialize_api_json(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    return obj

class StrictContainer:

    FIELDS = {}
    ALLOW_EMPTY = ("",)
    PARENT_KEYVAL = ("", "")

    def __init__(self, **kwargs):
        if kwargs:
            self._loaded = kwargs
            self._load()
        else:
            self._loaded = {}

    def _verify_keys(self):
        missing = []
        for key in self.FIELDS:
            if key not in self._loaded:
                if key not in self.ALLOW_EMPTY:
                    missing.append(key)
                else:
                    # Create the key and empty value of the type of the
                    # missing key. If the expected type is one or more
                    # StrictContainers, initialize the key with an empty dict
                    expected_type = self.FIELDS[key]

                    if isinstance(expected_type, tuple):
                        self._loaded[key] = {}
                    elif issubclass(expected_type, StrictContainer):
                        self._loaded[key] = {}
                    else:
                        self._loaded[key]= self.FIELDS[key]()

        if missing:
            raise KeyError(f"{', '.join(missing)}")

    def _verify_key_types(self):
        errors = []
        for key in self.FIELDS.keys():
            try:
                self._verify_key_type(key, self._loaded[key])
            except TypeError as e:
                errors.append(str(e))

        if errors:
            raise TypeError(f"{', '.join(errors)}")

    def _verify_key_type(self, key, type_instance):
        # We only want to verify types for keys that have actually been defined
        # in the fields attribute. We don't care about other keys.
        expected_type = self.FIELDS.get(key)
        if not expected_type:
            return

        # If the expected type is a Cuckoo JSON file or tuple
        # (multiple possible Cuckoo JSON files), set the type to verify to
        # a dict, as the type should still currently be a dict as we have
        # not created its expected type object yet.
        if not isinstance(type_instance, expected_type):
            if isinstance(expected_type, tuple):
                expected_type = dict
            elif issubclass(expected_type, StrictContainer):
                expected_type = dict

        if not isinstance(type_instance, expected_type):
            raise TypeError(
                f"Value of key '{key}' must be {expected_type.__name__}. "
                f"Found {type_instance.__class__.__name__}"
            )

    def _create_child_type(self, child_type, key):
        try:
            self._loaded[key] = child_type(**self._loaded[key])
        except KeyError as e:
            raise KeyError(
                f"Key '{key}' is missing subkeys: {e}"
            ).with_traceback(e.__traceback__)
        except TypeError as e:
            raise TypeError(
                f"Key '{key}' has subkeys with invalid values: {e}"
            ).with_traceback(e.__traceback__)

    def _create_child_types(self):
        # For each expected type we have, verify if it is another Cuckoo JSON
        # file type. If it is, create an instance of it using the dictionary
        # with data that currently resides in its key. Replace the dict with
        # the type instance. Do the same for all its children
        for key, expected_type in self.FIELDS.items():
            type_instance = self._loaded[key]

            # If the value is already an instance of the type, skip it.
            if isinstance(type_instance, expected_type):
                continue

            # If a key is and is allowed to be empty, skip it.
            if not type_instance and key in self.ALLOW_EMPTY:
                continue

            # If a key can have more than 1 type, it must be in a tuple. If
            # a field can have more than 1 type, these types must specify
            # the key and value that decide what type must be chosen in their
            # PARENT_KEYVAL attribute.
            if isinstance(expected_type, tuple):
                for type_entry in expected_type:
                    if issubclass(type_entry, StrictContainer):

                        # Find the value of the key specified by this potential
                        # child key type. If it matches, choose this type class
                        parent_key, parent_val = type_entry.PARENT_KEYVAL
                        if self._loaded[parent_key] == parent_val:
                            self._create_child_type(type_entry, key)

            elif issubclass(expected_type, StrictContainer):
                self._create_child_type(expected_type, key)

    def _load(self):
        # First verify if all keys exist
        self._verify_keys()
        # Verify if all types are the expected type
        self._verify_key_types()
        # Load al CuckooJSONFile subtypes
        self._create_child_types()
        # Check if the values meet the constraints
        self.check_constraints()

    @classmethod
    def from_file(cls, filepath):
        try:
            with open(filepath, "r") as fp:
                loaded = json.load(
                    fp, object_hook=deserialize_disk_json
                )
        except json.decoder.JSONDecodeError as e:
            raise ValueError(f"JSON decoding error: {e}")

        return cls(**loaded)

    def check_constraints(self):
        pass

    def to_dict(self):
        return {
            k: v.to_dict() if isinstance(v, StrictContainer) else v for k, v in
            self._loaded.items()
        }

    def to_api_json(self):
        return json.dumps(self.to_dict(), default=serialize_api_json)

    def to_file(self, path):
        with open(path, "w") as fp:
            json.dump(self.to_dict(), fp, default=serialize_disk_json)

    def to_file_safe(self, path):
        fd, tmppath = mkstemp()
        with os.fdopen(fd, "w") as fp:
            json.dump(self.to_dict(), fp, default=serialize_disk_json)

        move(tmppath, path)

    def update(self, values):
        if not isinstance(values, dict):
            raise TypeError(
                f"Values must be a dictionary. Got: {type(values)}"
            )

        current_copy = self._loaded.copy()
        current_copy.update(values)
        self.__class__(**current_copy)
        self._loaded = current_copy

    def __getattr__(self, item):
        if item in self.__dict__.get("_loaded", {}):
            return self._loaded[item]

        return super().__getattribute__(item)

    def __setattr__(self, key, value):
        # TODO add type checking
        if key in self.__dict__.get("_loaded", {}):
            self._loaded[key] = value
        else:
            super().__setattr__(key, value)


class Settings(StrictContainer):

    FIELDS = {
        "timeout": int,
        "enforce_timeout": bool,
        "dump_memory": bool,
        "priority": int,
        "options": dict,
        "machine_tags": list,
        "platforms": list,
        "machines": list,
        "manual": bool
    }

class Errors(StrictContainer):

    FIELDS = {
        "errors": list,
        "fatal": list
    }

    def merge_errors(self, errors_container):
        self._loaded["errors"].extend(errors_container.errors)
        self._loaded["fatal"].extend(errors_container.fatal)

class SubmittedFile(StrictContainer):

    # Look at the parent dict and find the category key. Use this class if
    # the value is 'file'
    PARENT_KEYVAL = ("category", "file")
    FIELDS = {
        "filename": str,
        "size": int,
        "md5": str,
        "sha1": str,
        "sha256": str,
        "media_type": str,
        "type": str,
        "category": str
    }

class SubmittedURL(StrictContainer):

    # Look at the parent dict and find the category key. Use this class if
    # the value is 'file'
    PARENT_KEYVAL = ("category", "url")
    FIELDS = {
        "url": str,
        "category": str
    }

class Analysis(StrictContainer):

    FIELDS = {
        "id": str,
        "kind": str,
        "settings": Settings,
        "created_on": datetime.datetime,
        "category": str,
        "submitted": (SubmittedFile, SubmittedURL)
    }

class Task(StrictContainer):

    FIELDS = {
        "id": str,
        "analysis_id": str,
        "kind": str,
        "number": int,
        "platform": str,
        "os_version": str,
        "machine_tags": list,
        "machine": str,
        "errors": Errors
    }
    ALLOW_EMPTY = ("machine", "machine_tags", "os_version", "errors")

class TargetFile(StrictContainer):

    PARENT_KEYVAL = ("category", "file")
    FIELDS = {
        "filename": str,
        "platforms": list,
        "machine_tags": list,
        "size": int,
        "filetype": str,
        "media_type": str,
        "sha256": str,
        "extrpath": list,
        "container": bool
    }
    ALLOW_EMPTY = ("extrpath", "machine_tags")

class TargetURL(StrictContainer):

    PARENT_KEYVAL = ("category", "url")
    FIELDS = {
        "url": str,
        "platforms": list,
        "machine_tags": list
    }
    ALLOW_EMPTY = ("machine_tags",)

class Identification(StrictContainer):

    FIELDS = {
        "selected": bool,
        "target": (TargetFile, TargetURL),
        "category": str,
        "ignored": list,
        "parent": str,
        "errors": Errors
    }
    ALLOW_EMPTY = ("target", "parent", "ignored", "errors")

class Pre(StrictContainer):
    FIELDS = {
        "errors": Errors
    }
    ALLOW_EMPTY = ("errors",)