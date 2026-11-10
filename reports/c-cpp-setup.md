# Local C/C++ compiler setup

The portal implements C17 and C++20 workspaces and runs programs exclusively through local Docker Linux containers. It never runs submitted programs using the host GCC/G++ installation.

## Windows setup

1. In an Administrator PowerShell, run `wsl --install --no-distribution`. Restart Windows if requested. On older Windows builds, follow Microsoft's WSL installation instructions to enable virtualization components.
2. Install Docker Desktop using the WSL 2 backend: https://docs.docker.com/desktop/setup/install/windows-install/
3. Start Docker Desktop and wait until the Linux engine is running.
4. From this project folder, run `powershell -ExecutionPolicy Bypass -File .\tools\Prepare-CppSandbox.ps1` to pull the compiler image.
5. Open C or C++ from Online Compiler and select **Check runner**. Run the sample program and confirm `Hello, world!`.

The service must run as the Windows user who can access Docker Desktop. Defaults: Docker context `desktop-linux`, image `gcc:14.2.0`. Optional environment variables are `C_COMPILER_DOCKER_CONTEXT` and `C_COMPILER_IMAGE`. On Linux the default context is `default`.

## Limits and behavior

Single source file, C17 or C++20, 64 KB source and 32 KB stdin. A fresh container compiles and runs each request. Runtime limit is 5 seconds, total compilation/run limit is 25 seconds, memory 256 MB, one CPU, 64 processes, bounded output 64 KB, three concurrent requests, one active request per user. Containers run as an unprivileged user, have no network, a read-only root filesystem, dropped capabilities, no-new-privileges, and restricted temporary filesystems. Only the request's temporary source directory is mounted read-only. Containers are removed after each request. No automatic image downloads happen during student execution.

The editor supports local per-account drafts, source import/download and standard input. Source code stays on this server; no external compiler service is called. Compile diagnostics, runtime errors and limit errors are returned separately. The editor is accessible while setup is pending, and Compile & Run remains disabled until Docker and its image are ready.

## Verification

`python -B manage.py test workbench.tests_native_compiler --noinput`

Browser checks use isolated response fixtures, not a live Docker engine. Live sandbox smoke tests must be completed after Docker installation: hello world in C/C++, input/output program, compilation error, infinite loop, excessive output, and denied host/network access.
