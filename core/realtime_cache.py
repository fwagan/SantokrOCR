"""
Real-time frame cache with async writer thread.

Manages session directories under %APPDATA%/SantokrOCR/RealTimeProcessCache/,
saves frames as JPEG via background writer thread, and supports .srlog export.
"""

import os
import queue
import random
import shutil
import string
import threading
import time

import cv2

from data.serializers.srlog import SrlogSerializer


class RealTimeProcessCache:
    """Async frame cache with session management."""

    def __init__(self):
        app_data = os.environ.get('APPDATA', '')
        self.base_dir = os.path.join(app_data, 'SantokrOCR', 'RealTimeProcessCache')
        self._session_dir = None
        self._write_queue = None
        self._writer_thread = None
        self._writer_running = False

    def _ensure_base_dir(self):
        os.makedirs(self.base_dir, exist_ok=True)

    def _generate_session_name(self):
        ts = time.strftime('%Y%m%d_%H%M%S')
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        return f"{ts}_{suffix}"

    def start_writer(self):
        """Create session directory and start background writer thread."""
        self._ensure_base_dir()
        name = self._generate_session_name()
        self._session_dir = os.path.join(self.base_dir, name)
        os.makedirs(self._session_dir, exist_ok=True)

        self._write_queue = queue.Queue(maxsize=500)
        self._writer_running = True
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def _writer_loop(self):
        """Background thread: reads frames from queue and writes JPEG files."""
        session_dir = self._session_dir
        while True:
            try:
                item = self._write_queue.get(timeout=0.5)
            except queue.Empty:
                if not self._writer_running:
                    break
                continue

            if item is None:
                break

            frame_num, frame_bgr = item
            try:
                _, jpg_data = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                path = os.path.join(session_dir, f"{frame_num:06d}.jpg")
                with open(path, 'wb') as f:
                    f.write(jpg_data)
            except Exception as e:
                print(f"[cache] write error for frame {frame_num}: {e}")

    def save_frame(self, frame_bgr, frame_num):
        """Non-blocking push to write queue. Drops frame if queue is full."""
        q = self._write_queue
        if q is None:
            return
        try:
            q.put_nowait((frame_num, frame_bgr))
        except queue.Full:
            pass

    def stop_writer(self):
        """Flush queue and stop writer thread. Does NOT delete session files."""
        if not self._writer_running:
            return
        self._writer_running = False
        if self._write_queue is not None:
            try:
                self._write_queue.put_nowait(None)
            except queue.Full:
                pass
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=5)
        self._write_queue = None
        self._writer_thread = None

    def clear(self):
        """Stop writer and delete entire session directory."""
        self.stop_writer()
        if self._session_dir and os.path.isdir(self._session_dir):
            shutil.rmtree(self._session_dir, ignore_errors=True)
        self._session_dir = None

    def has_session(self) -> bool:
        return self._session_dir is not None and os.path.isdir(self._session_dir)

    def session_dir(self) -> str:
        return self._session_dir

    def load_frame(self, frame_num):
        """Load frame from cache. Returns BGR ndarray or None."""
        if not self._session_dir:
            return None
        path = os.path.join(self._session_dir, f"{frame_num:06d}.jpg")
        if os.path.exists(path):
            return cv2.imread(path)
        return None

    def has_frame(self, frame_num) -> bool:
        if not self._session_dir:
            return False
        path = os.path.join(self._session_dir, f"{frame_num:06d}.jpg")
        return os.path.exists(path)

    def cached_count(self) -> int:
        """Return number of cached JPEG files (approximate)."""
        if not self._session_dir or not os.path.isdir(self._session_dir):
            return 0
        count = 0
        try:
            for fname in os.listdir(self._session_dir):
                if fname.endswith('.jpg'):
                    count += 1
        except OSError:
            pass
        return count

    def export_as_srlog(self, output_path, results, rois, interval=0.25,
                        rotate_angle=5, source="", events=None,
                        progress_callback=None):
        """Export current session as .srlog (ZIP) file.

        Args:
            output_path: 输出文件路径
            progress_callback: 进度回调 callback(current, total)，
                透传给 SrlogSerializer.write
        """
        if not self._session_dir or not os.path.isdir(self._session_dir):
            raise RuntimeError("No session to export")

        SrlogSerializer.write(
            output_path=output_path,
            results=results,
            rois=rois,
            interval=interval,
            rotate_angle=rotate_angle,
            source=source,
            events=events,
            frames_dir=self._session_dir,
            progress_callback=progress_callback,
        )

    @staticmethod
    def cleanup_old_sessions(max_keep=5):
        """Remove all but the N most recent session directories."""
        app_data = os.environ.get('APPDATA', '')
        base_dir = os.path.join(app_data, 'SantokrOCR', 'RealTimeProcessCache')
        if not os.path.isdir(base_dir):
            return

        sessions = []
        for name in os.listdir(base_dir):
            path = os.path.join(base_dir, name)
            if os.path.isdir(path):
                try:
                    mtime = os.path.getmtime(path)
                    sessions.append((mtime, path))
                except OSError:
                    pass

        sessions.sort(key=lambda x: x[0], reverse=True)
        for _, path in sessions[max_keep:]:
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception as e:
                print(f"[cache] cleanup error: {e}")
