import asyncio
import os.path
import tempfile312 as tempfile
from dataclasses import dataclass
from subprocess import CalledProcessError

from ofrak.component.packer import Packer
from ofrak.component.unpacker import Unpacker, UnpackerError
from ofrak.resource import Resource
from ofrak.core.binary import GenericBinary
from ofrak.core.filesystem import FilesystemRoot, Folder, File, SpecialFileType
from ofrak.core.magic import MagicMimePattern, MagicDescriptionPattern

from ofrak.model.component_model import ComponentExternalTool
from ofrak.model.component_model import ComponentConfig
from ofrak_type.range import Range


TAR = ComponentExternalTool("tar", "https://www.gnu.org/software/tar/", "--help", apt_package="tar")


@dataclass
class TarArchive(GenericBinary, FilesystemRoot):
    """
    Filesystem stored in a tar archive.
    """


class TarUnpacker(Unpacker[None]):
    """
    Unpack a tar archive.
    """

    targets = (TarArchive,)
    children = (File, Folder, SpecialFileType)
    external_dependencies = (TAR,)

    async def unpack(self, resource: Resource, config: ComponentConfig = None) -> None:
        # Write the archive data to a file
        async with resource.temp_to_disk(suffix=".tar") as temp_archive_path:
            # Check the archive member files to ensure none unpack to a parent directory
            cmd = [
                "tar",
                "-P",
                "-tf",
                temp_archive_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode:
                raise CalledProcessError(returncode=proc.returncode, cmd=cmd)
            for filename in stdout.decode().splitlines():
                # Handles relative parent paths and rooted paths, and normalizes paths like "./../"
                rel_filename = os.path.relpath(filename)
                if rel_filename.startswith(".." + os.sep):
                    raise UnpackerError(
                        f"Tar archive contains a file {filename} that would extract to a parent "
                        f"directory {rel_filename}."
                    )

            # Unpack into a temporary directory using the temporary file
            with tempfile.TemporaryDirectory() as temp_dir:
                command = ["tar", "--xattrs", "-C", temp_dir, "-xf", temp_archive_path]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                )
                returncode = await proc.wait()
                if returncode:
                    raise CalledProcessError(returncode=returncode, cmd=command)

                # Initialize a filesystem from the unpacked/untarred temporary folder
                tar_view = await resource.view_as(TarArchive)
                await tar_view.initialize_from_disk(temp_dir)


class TarPacker(Packer[None]):
    """
    Pack files into a tar archive.
    """

    targets = (TarArchive,)
    external_dependencies = (TAR,)

    async def pack(self, resource: Resource, config: ComponentConfig = None) -> None:
        # Flush the child files to the filesystem
        tar_view = await resource.view_as(TarArchive)
        flush_dir = await tar_view.flush_to_disk()

        # Pack it back into a temporary archive
        with tempfile.NamedTemporaryFile(suffix=".tar", delete_on_close=False) as temp_archive:
            temp_archive.close()
            cmd = [
                "tar",
                "--xattrs",
                "-C",
                flush_dir,
                "-cf",
                temp_archive.name,
                ".",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
            )
            returncode = await proc.wait()
            if proc.returncode:
                raise CalledProcessError(returncode=returncode, cmd=cmd)

            # Replace the original archive data
            with open(temp_archive.name, "rb") as new_fh:
                resource.queue_patch(Range(0, await resource.get_data_length()), new_fh.read())


MagicMimePattern.register(TarArchive, "application/x-tar")
MagicDescriptionPattern.register(TarArchive, lambda s: "tar archive" in s.lower())
