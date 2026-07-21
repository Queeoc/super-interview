@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ====================================
echo        super-interview 服务启动
echo ====================================
echo.

set CONDA_ENV_NAME=biz_agent

echo [1/5] 检�?Conda 环境...

where conda >nul 2>&1
if not errorlevel 1 (
    echo [成功] �?PATH 中找�?conda
    goto :conda_ready
)

if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" (
    set "PATH=%USERPROFILE%\miniconda3\Scripts;%USERPROFILE%\miniconda3;%USERPROFILE%\miniconda3\Library\bin;%PATH%"
    echo [成功] 找到 conda: %USERPROFILE%\miniconda3
    goto :conda_ready
)
if exist "%USERPROFILE%\Anaconda3\Scripts\conda.exe" (
    set "PATH=%USERPROFILE%\Anaconda3\Scripts;%USERPROFILE%\Anaconda3;%USERPROFILE%\Anaconda3\Library\bin;%PATH%"
    echo [成功] 找到 conda: %USERPROFILE%\Anaconda3
    goto :conda_ready
)
if exist "C:\ProgramData\miniconda3\Scripts\conda.exe" (
    set "PATH=C:\ProgramData\miniconda3\Scripts;C:\ProgramData\miniconda3;C:\ProgramData\miniconda3\Library\bin;%PATH%"
    echo [成功] 找到 conda: C:\ProgramData\miniconda3
    goto :conda_ready
)

echo [错误] 未找�?Conda，请先安�?Miniconda
echo [提示] https://docs.anaconda.com/miniconda/install/
pause
exit /b 1

:conda_ready
echo.

echo [2/5] 获取 Python 路径...
for /f "tokens=*" %%i in ('conda run -n %CONDA_ENV_NAME% where python 2^>nul') do set PYTHON_CMD=%%i
if not defined PYTHON_CMD (
    echo [错误] 无法获取 conda 环境中的 Python 路径，请先创建环境：
    echo        conda create -n %CONDA_ENV_NAME% python=3.11 -y
    echo        pip install -e .
    pause
    exit /b 1
)
echo [信息] Python: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

echo [3/5] 启动 Milvus 向量数据�?..

where docker >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找�?Docker，请先安�?Docker Desktop
    pause
    exit /b 1
)

docker ps --format "{{.Names}}" 2>nul | findstr "milvus-standalone" >nul 2>&1
if not errorlevel 1 (
    echo [信息] Milvus 容器已在运行
) else (
    docker compose -f vector-database.yml up -d
    if errorlevel 1 (
        echo [错误] Docker 启动失败，请确认 Docker Desktop 已启�?
        pause
        exit /b 1
    )
    echo [信息] 等待 Milvus 启动 (10�?...
    timeout /t 10 /nobreak >nul
)
echo [成功] Milvus 数据库就�?
echo.

echo [4/5] 启动 CLS MCP 服务...
start "CLS MCP Server" /min %PYTHON_CMD% mcp_servers/cls_server.py
timeout /t 2 /nobreak >nul
echo [成功] CLS MCP 服务已启�?
echo.

echo [5/5] 启动 Monitor MCP 服务...
start "Monitor MCP Server" /min %PYTHON_CMD% mcp_servers/monitor_server.py
timeout /t 2 /nobreak >nul
echo [成功] Monitor MCP 服务已启�?
echo.

echo [启动] 启动 FastAPI 服务...
start "super-interview API" %PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 9900
echo [信息] 等待服务启动 (15�?...
timeout /t 15 /nobreak >nul
echo.

echo [信息] 检查服务状�?..
curl -s http://localhost:9900/health >nul 2>&1
if errorlevel 1 (
    echo [警告] 服务可能尚未就绪，跳过文档上�?
) else (
    echo [成功] FastAPI 服务运行正常
    echo.
    echo [上传] 上传文档到向量数据库...
    for %%f in (knowledge_base\rubrics\*.md) do (
        echo   上传: %%~nxf
        curl -s -X POST http://localhost:9900/api/upload -F "file=@%%f" >nul 2>&1
    )
    echo [成功] 文档上传完成
)

echo.
echo ====================================
echo   服务启动完成�?
echo ====================================
echo.
echo  Web:      http://localhost:9900
echo  API文档:  http://localhost:9900/docs
echo  Milvus:   http://localhost:8000
echo.
echo  Conda env: %CONDA_ENV_NAME%
echo  启动命令:  conda activate %CONDA_ENV_NAME%
echo.
echo  日志:
echo    FastAPI  : logs\app_*.log
echo    CLS MCP  : type mcp_cls.log
echo    Monitor  : type mcp_monitor.log
echo.
echo  停止服务:  stop-windows.bat
echo ====================================
pause
