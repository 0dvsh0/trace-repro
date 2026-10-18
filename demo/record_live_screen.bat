@echo off
setlocal
cd /d "%~dp0\.."

echo ======================================================================
echo   trace-repro — Live Hackathon Demo Screen Recorder
echo ======================================================================
echo.
echo  This tool records raw, 1080p, 30fps screen video with mouse movements.
echo  Zero static images. Zero fake slideshows. 100%% pure live screen video.
echo.
echo  Recommended steps while recording:
echo    1. Show the payment checkout crash in Chrome.
echo    2. Switch to FalkorDB on localhost:3000 -> run the collision Cypher query.
echo    3. Switch to GitLab -> show the unsolved failing job log.
echo    4. Switch to Terminal -> run 'python demo/showcase.py' (watch RED to GREEN).
echo    5. Switch to GitLab -> show the solved Merge Request !1 and Code Quality.
echo.
echo  Press [Enter] to START recording...
pause > nul
echo.
echo  ======================================================================
echo   🔴 RECORDING STARTED!
echo   When finished, press [q] in this window to stop and finalize the video.
echo  ======================================================================
echo.

ffmpeg -y -f gdigrab -framerate 30 -draw_mouse 1 -i desktop -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p "demo\raw_live_demo.mp4"

echo.
echo ======================================================================
echo   ✅ RECORDING COMPLETE!
echo   Saved video: demo\raw_live_demo.mp4
echo ======================================================================
echo.
pause
