# Building flash-attention on Windows

This is optional but improves throughput by a lot so I recommend doing it. (Tested ~2x speedup for deepseek.)

There are prebuilt wheels floating around, but I was too afraid of getting infected by malware, so I decided to build it from source. I am recording what I did and the errors I encountered on Feb 13 2026, so if you encounter the same errors in the future, my solutions might help.

First get the environment set up:
- Install CUDA 12.8 from nvidia
  - You do not need NSight for this, it's a few gigs of dead weight.
- Install MSVC Build Tools 2022 from Microsoft
  - Do not install the latest version, or it will complain that CUDA is only compatible/tested against MSVC versions 2019-2022.
- Set `CUDA_HOME` = `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8`
  - CUDA installer sets `CUDA_PATH` but the compilation script asks for `CUDA_HOME`.
- [Enable long paths in Windows](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation?tabs=powershell#registry-setting-to-enable-long-paths)
  - Otherwise git or pip will complain about the path being too long/unable to write.

Then if you install flash attention from pypi now,
```
pip install flash-attn --no-build-isolation
```

It will give:
```
      cl : Command line warning D9002 : ignoring unknown option '-O3'
      cl : Command line warning D9002 : ignoring unknown option '-std=c++17'
      ... error C2039: 'is_unsigned_v': is not a member of 'cutlass::platform'
```

Looking at the source, it seems to require DISTUTILS_USE_SDK set to 1 for windows, but doing so will tell us:
```
      Error checking compiler version for cl: [WinError 2] The system cannot find the file specified
```

AI recommended trying the same thing in a Visual Studio dev shell, which also did not work for me.  
So a little elbow grease:
```
cd ..
gh repo clone Dao-AILab/flash-attention
```

Remove the `DISTUTILS_USE_SDK` check inside `setup.py` and install
```
pip install . --no-build-isolation
```

That did the trick for me. The build took about an hour on my machine, so you might want to grab a meal or something while you wait.