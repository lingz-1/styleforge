# StyleForge workspace instructions

- This project uses `D:\anaconda\envs\style\python.exe`. This project-specific environment choice overrides any generic Python environment default because the installed CUDA, FashionCLIP, API, and test dependencies are in `style`.
- Do not use bare `python` or `python3` commands.
- Tell the user before running tests, imports, embedding jobs, servers, or dependency installation; let the user run them unless they explicitly ask the agent to execute.
- Treat `E:\image.tar\image\images` and `E:\style-dataset` as external read-only dataset roots. Do not move, rename, delete, extract into, or write generated artifacts under them.
- Store SQLite files, reports, embeddings, and indexes only inside this workspace unless the user explicitly chooses another non-dataset output directory.
- Use the Tsinghua PyPI mirror for ordinary Python dependencies and the approved Aliyun PyTorch wheel source for CUDA PyTorch packages.
