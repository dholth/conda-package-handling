"""The 'new' conda format, introduced in late 2018/early 2019.  Spec at
https://anaconda.atlassian.net/wiki/spaces/AD/pages/90210540/Conda+package+format+v2"""

import json
import os
import tarfile
from zipfile import ZIP_STORED, ZipFile

import zstandard

from . import utils
from .interface import AbstractBaseFormat
from .streaming import _extract

CONDA_PACKAGE_FORMAT_VERSION = 2
DEFAULT_COMPRESSION_TUPLE = (".tar.zst", "zstd", "zstd:compression-level=22")

# increase to reduce speed and increase compression (22 = conda's default)
ZSTD_COMPRESS_LEVEL = 22
# increase to reduce compression (slightly) and increase speed
ZSTD_COMPRESS_THREADS = 1


class CondaFormat_v2(AbstractBaseFormat):
    """If there's another conda format or breaking changes, please create a new class and keep this
    one, so that handling of v2 stays working."""

    @staticmethod
    def supported(fn):
        return fn.endswith(".conda")

    @staticmethod
    def extract(fn, dest_dir, **kw):
        components = utils.ensure_list(kw.get("components")) or ("info", "pkg")
        if not os.path.isabs(fn):
            fn = os.path.normpath(os.path.join(os.getcwd(), fn))
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)

        _extract(str(fn), str(dest_dir), components=components)

    @staticmethod
    def extract_info(fn, dest_dir=None):
        return CondaFormat_v2.extract(fn, dest_dir, components=["info"])

    @staticmethod
    def create(
        prefix,
        file_list,
        out_fn,
        out_folder=os.getcwd(),
        compressor=lambda: zstandard.ZstdCompressor(
            level=ZSTD_COMPRESS_LEVEL, threads=ZSTD_COMPRESS_THREADS
        ),
        **kw,
    ):
        if os.path.isabs(out_fn):
            out_folder = os.path.dirname(out_fn)
            out_fn = os.path.basename(out_fn)
        conda_pkg_fn = os.path.join(out_folder, out_fn)
        file_id = out_fn = out_fn.replace(".conda", "")
        pkg_files = utils.filter_info_files(file_list, prefix)
        info_files = set(file_list) - set(pkg_files)

        # legacy libarchive-ish compatibility
        ext, comp_filter, filter_opts = compression_tuple = kw.get(
            "compression_tuple", (None, None, None)
        )
        if filter_opts and filter_opts.startswith("zstd:compression-level="):
            compressor = lambda: zstandard.ZstdCompressor(
                level=int(filter_opts.split("=", 1)[-1]),
                threads=ZSTD_COMPRESS_THREADS,
            )

        with ZipFile(conda_pkg_fn, "w", compression=ZIP_STORED) as conda_file, utils.tmp_chdir(
            prefix
        ):

            pkg_metadata = {"conda_pkg_format_version": CONDA_PACKAGE_FORMAT_VERSION}
            conda_file.writestr("metadata.json", json.dumps(pkg_metadata))

            # put the info last, for parity with updated transmute.
            for component, files in (f"pkg-{file_id}.tar.zst", pkg_files), (
                f"info-{file_id}.tar.zst",
                info_files,
            ):
                compress = compressor()
                with conda_file.open(component, "w") as component_file:
                    component_stream = compress.stream_writer(component_file, closefd=False)
                    component_tar = tarfile.TarFile(fileobj=component_stream, mode="w")

                    for file in files:
                        component_tar.add(file, filter=utils.anonymize_tarinfo)

                    component_tar.close()
                    component_stream.close()

        return conda_pkg_fn

    @staticmethod
    def get_pkg_details(in_file):
        stat_result = os.stat(in_file)
        size = stat_result.st_size
        md5, sha256 = utils.checksums(in_file, ("md5", "sha256"))
        return {"size": size, "md5": md5, "sha256": sha256}
