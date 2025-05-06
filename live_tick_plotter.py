import asyncio
from ib_insync import *
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import matplotlib.dates as mdates
import numpy as np
from datetime import datetime, timedelta
import logging
import pandas as pd
import os
from scipy.fftpack import fft, ifft, fftfreq
from matplotlib.widgets import Button, Slider
import glob
import time
import threading
import queue
from concurrent.futures import ThreadPoolExecutor

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set Matplotlib to use a non-interactive backend if we're experiencing UI freezes
plt.switch_backend('TkAgg')  # Try 'Agg', 'Qt5Agg', or 'TkAgg' if one doesn't work

class LiveTickPlotter:
    def __init__(self, symbol, exchange='SMART', currency='USD'):
        self.ib = IB()
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency
        self.contract = None
        self.prices = []
        self.times = []
        self.volumes = []
        self.last_volume = None
        self.initialized = False
        self.last_update = None
        self.last_save = None
        self.data_dir = f"{symbol}_tick_data"
        self.connection_task = None
        self.is_connected = False
        self.connection_retry_delay = 5  # seconds between retry attempts
        self.max_retries = 10  # maximum number of retry attempts
        self.current_minute_data = {
            'times': [],
            'prices': [],
            'volumes': []
        }
        self.current_bid = None
        self.current_ask = None
        self.bid_ask_text = None
        
        # FFT related variables
        self.fft_data = None
        self.fft_fit_line = None
        self.fft_x_pred = None
        self.n_components = 5  # Number of frequency components to use for the fit line
        
        # Playback related variables
        self.playback_mode = False
        self.playback_data = None
        self.playback_index = 0
        self.playback_speed = 1.0
        self.playback_paused = True
        self.playback_last_time = None
        
        # Threading related variables
        self.thread_pool = ThreadPoolExecutor(max_workers=2)
        self.fft_queue = queue.Queue()
        self.fft_thread_active = False
        self.plot_lock = threading.Lock()
        self.ui_event_lock = threading.Lock()
        
        # Create data directory if it doesn't exist
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        # Zoom control variables
        self.zoom_factor = 1.0
        self.base_window_size = 100  # Base number of points to show
        self.current_window_size = self.base_window_size
        
        # Set up the figure with non-blocking mode
        self.setup_figure()
        
        # Bootstrap with saved data (in background)
        self.thread_pool.submit(self.bootstrap_from_saved_data)
        
        self.option_subscription_initialized = False
        self.selected_option_contract = None
        self.selected_option_data = {
            'strike': None,
            'right': None,
            'expiry': None,
            'bid': None,
            'ask': None,
            'bidSize': None,
            'askSize': None
        }
    
    def setup_figure(self):
        """Set up the plot with interactive elements in a way that doesn't block"""
        plt.ion()  # Turn on interactive mode
        self.fig = plt.figure(figsize=(12, 10))
        
        # Use a tight layout with padding to prevent control area overlap
        self.fig.tight_layout(pad=3.0)
        
        # Set up the grid for the main plots and control elements
        self.gs = self.fig.add_gridspec(3, 1, height_ratios=[3, 1, 0.5])
        
        self.ax1 = self.fig.add_subplot(self.gs[0])  # Price plot
        self.ax2 = self.fig.add_subplot(self.gs[1])  # Volume plot
        self.ax_controls = self.fig.add_subplot(self.gs[2])  # Control area
        
        # Price plot
        self.price_line, = self.ax1.plot([], [], 'b-')
        self.fft_line, = self.ax1.plot([], [], 'r--', linewidth=1.5, alpha=0.7)
        self.ax1.set_title(f'Live Tick Data: {self.symbol} (Press +/- to zoom, r to reset)')
        self.ax1.set_ylabel('Price')
        self.ax1.grid(True)
        
        # Volume plot
        self.volume_bars = self.ax2.bar([], [], color='g', alpha=0.5)
        self.ax2.set_ylabel('Volume')
        self.ax2.grid(True)
        
        # Format x-axis for both plots
        for ax in [self.ax1, self.ax2]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        
        # Setup playback controls
        self.setup_playback_controls()
        
        # Connect keyboard events
        self.fig.canvas.mpl_connect('key_press_event', self.on_key_press)
        
        # Needed for non-blocking UI updates
        plt.tight_layout()
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()
    
    def setup_playback_controls(self):
        """Set up UI controls for data playback"""
        # Clear any existing axes elements
        self.ax_controls.clear()
        self.ax_controls.set_frame_on(False)  # Hide frame
        self.ax_controls.set_xticks([])  # Hide x ticks
        self.ax_controls.set_yticks([])  # Hide y ticks
        
        # Create buttons for playback control with adjusted positions to prevent overlap
        rewind_ax = plt.axes([0.1, 0.05, 0.1, 0.04])
        play_pause_ax = plt.axes([0.25, 0.05, 0.1, 0.04])
        load_data_ax = plt.axes([0.4, 0.05, 0.1, 0.04])
        live_mode_ax = plt.axes([0.55, 0.05, 0.1, 0.04])
        
        # Create slider for speed control
        speed_slider_ax = plt.axes([0.7, 0.05, 0.2, 0.04])
        
        # Initialize the buttons
        self.rewind_button = Button(rewind_ax, 'Rewind')
        self.play_pause_button = Button(play_pause_ax, 'Play')
        self.load_data_button = Button(load_data_ax, 'Load Data')
        self.live_mode_button = Button(live_mode_ax, 'Live Mode')
        
        # Initialize the slider
        self.speed_slider = Slider(
            speed_slider_ax, 'Speed', 0.1, 10.0, 
            valinit=1.0, valstep=0.1, 
            color='green'
        )
        
        # Connect events with locks to prevent simultaneous access
        self.rewind_button.on_clicked(self.on_rewind)
        self.play_pause_button.on_clicked(self.on_play_pause)
        self.load_data_button.on_clicked(self.on_load_data)
        self.live_mode_button.on_clicked(self.on_live_mode)
        self.speed_slider.on_changed(self.on_speed_change)
        
        # Show playback status
        self.playback_status_text = self.ax_controls.text(
            0.5, 0.7, "Live Mode", 
            horizontalalignment='center', 
            verticalalignment='center',
            fontsize=12
        )
    
    def bootstrap_from_saved_data(self):
        """Load recent data files to bootstrap the analysis - runs in separate thread"""
        try:
            self.update_playback_status("Bootstrapping from saved data...")
            
            csv_files = [f for f in os.listdir(self.data_dir) if f.endswith('.csv')]
            if not csv_files:
                logger.info("No saved data files found for bootstrapping")
                self.update_playback_status("No saved data found")
                return
            
            # Sort files by timestamp to get the most recent ones
            csv_files.sort(reverse=True)
            
            # Initialize data structures
            all_times = []
            all_prices = []
            all_volumes = []
            
            # Load the most recent files (limit to 5 for speed during bootstrap)
            for i, file in enumerate(csv_files[:5]):  # Limit initial load for responsiveness
                try:
                    file_path = os.path.join(self.data_dir, file)
                    df = pd.read_csv(file_path)
                    
                    # Convert time strings to datetime objects
                    df['time'] = pd.to_datetime(df['time'])
                    
                    # Sort by time to ensure chronological order
                    df = df.sort_values('time')
                    
                    # Add to our data lists
                    all_times.extend(df['time'].tolist())
                    all_prices.extend(df['price'].tolist())
                    
                    # Handle volume - ensure we have volume deltas
                    if 'volume_delta' in df.columns:
                        all_volumes.extend(df['volume_delta'].tolist())
                    else:
                        # If we only have cumulative volume, compute deltas
                        vol_deltas = df['volume'].diff().fillna(0).tolist()
                        all_volumes.extend(vol_deltas)
                    
                    if i == 0:
                        logger.info(f"Bootstrapping from recent file: {file} with {len(df)} data points")
                except Exception as e:
                    logger.error(f"Error loading file {file}: {e}")
                    continue
            
            # Make sure data is chronologically sorted
            if all_times:
                # Update UI to show progress
                self.update_playback_status("Processing bootstrap data...")
                
                # Sort all data by time
                sorted_data = sorted(zip(all_times, all_prices, all_volumes), key=lambda x: x[0])
                
                # Unpack the sorted data
                with self.plot_lock:
                    self.times = [item[0] for item in sorted_data]
                    self.prices = [item[1] for item in sorted_data]
                    self.volumes = [item[2] for item in sorted_data]
                    
                    if self.volumes:
                        self.last_volume = sum(self.volumes[-100:])  # Use recent cumulative volume
                
                # Queue FFT analysis instead of doing it synchronously
                self.queue_fft_analysis()
                
                logger.info(f"Bootstrapped with {len(self.prices)} historical data points")
                self.update_playback_status("Ready")
            else:
                logger.warning("No valid data found for bootstrapping")
                self.update_playback_status("No valid bootstrap data")
                
        except Exception as e:
            logger.error(f"Error bootstrapping from saved data: {e}")
            self.update_playback_status("Bootstrap error")
    
    def queue_fft_analysis(self):
        """Add a request to the FFT analysis queue without blocking"""
        self.fft_queue.put(True)
        
        # Start the FFT thread if it's not already running
        if not self.fft_thread_active:
            self.fft_thread_active = True
            self.thread_pool.submit(self.fft_worker)
    
    def fft_worker(self):
        """Background worker that processes FFT requests from the queue"""
        try:
            while not self.fft_queue.empty():
                # Get the next item from the queue (doesn't matter what it is)
                self.fft_queue.get()
                
                # Only analyze if we have enough data
                with self.plot_lock:
                    if len(self.prices) < 20:
                        continue
                    
                    # Copy data to avoid thread conflicts
                    prices = self.prices.copy()
                    times = self.times.copy()
                
                # Do the actual FFT analysis (which can be CPU intensive)
                self.perform_fft_analysis_threaded(prices, times)
                
                # Mark task as done
                self.fft_queue.task_done()
                
            self.fft_thread_active = False
            
        except Exception as e:
            logger.error(f"Error in FFT worker thread: {e}")
            self.fft_thread_active = False
    
    def perform_fft_analysis_threaded(self, prices, times):
        """Thread-safe version of FFT analysis that works on copied data"""
        try:
            # Get price data - use most recent 1000 points max for better performance
            max_points = min(1000, len(prices))
            y = np.array(prices[-max_points:])
            
            # Need to convert datetime to numeric values for FFT
            recent_times = times[-max_points:]
            x = np.array([(t - recent_times[0]).total_seconds() for t in recent_times])
            
            # Check for valid time differences
            if len(x) < 2 or np.all(np.diff(x) == 0):
                logger.warning("Invalid time data for FFT analysis")
                return
                
            # Get FFT components
            N = len(y)
            yf = fft(y)
            xf = fftfreq(N, d=np.mean(np.diff(x)))  # Use average time step
            
            # Get positive frequencies only
            positive_freq_idxs = np.arange(1, N // 2)
            
            if len(positive_freq_idxs) < self.n_components:
                logger.warning(f"Not enough frequency components for analysis: {len(positive_freq_idxs)} < {self.n_components}")
                return
            
            # Get the most dominant frequencies (excluding DC component)
            amplitudes = 2.0/N * np.abs(yf[positive_freq_idxs])
            dominant_idxs = np.argsort(amplitudes)[-self.n_components:]
            
            # Reconstruct signal with only dominant frequencies
            yf_filtered = np.zeros(len(yf), dtype=complex)
            yf_filtered[0] = yf[0]  # Keep DC component (mean)
            
            for idx in dominant_idxs:
                actual_idx = positive_freq_idxs[idx]
                yf_filtered[actual_idx] = yf[actual_idx]
                # Add symmetric component for real signal
                yf_filtered[N-actual_idx] = yf[N-actual_idx]
            
            # Inverse FFT to get filtered signal
            y_filtered = ifft(yf_filtered).real
            
            # Generate future points for prediction
            if len(x) > 1:
                # Calculate the entire visible time range plus 5 seconds
                time_range = (times[-1] - times[0]).total_seconds()
                x_spacing = np.mean(np.diff(x))  # Use average time step
                
                # Calculate how far into the future we need to extend (to cover visible range + 5s)
                if time_range > 0:
                    # Create prediction timestamps from start of data to end of visible range + 5s
                    future_end_time = (times[-1] - times[0]).total_seconds() + 5.0
                    x_pred = np.arange(0, future_end_time, x_spacing)
                else:
                    # Fallback if time range calculation fails
                    future_points = 200  # Ensure we have plenty of points
                    x_pred = np.arange(0, x[-1] + (future_points * x_spacing), x_spacing)
                
                # Initialize prediction with the mean value
                y_mean = yf[0].real / N
                yf_pred = np.ones(len(x_pred)) * y_mean
                
                # Add each dominant frequency component
                for idx in dominant_idxs:
                    actual_idx = positive_freq_idxs[idx]
                    freq = xf[actual_idx]
                    amplitude = np.abs(yf[actual_idx]) * 2.0 / N
                    phase = np.angle(yf[actual_idx])
                    
                    # Use frequency, amplitude and phase to predict future values
                    pred_wave = amplitude * np.cos(2 * np.pi * freq * x_pred + phase)
                    yf_pred += pred_wave
                
                # Store the prediction data in a thread-safe way
                with self.plot_lock:
                    self.fft_data = y_filtered
                    self.fft_fit_line = yf_pred
                    self.fft_x_pred = x_pred
                
                logger.info(f"FFT analysis completed successfully with {self.n_components} components and {len(x_pred)} prediction points")
            
        except Exception as e:
            logger.error(f"Error in FFT analysis: {e}", exc_info=True)
    
    def update_playback_status(self, status_text):
        """Update the playback status text in a thread-safe way"""
        if hasattr(self, 'playback_status_text'):
            # Use the main thread to update the UI
            self.playback_status_text.set_text(status_text)
            # Try to update without blocking if possible
            try:
                self.fig.canvas.draw_idle()
            except:
                pass  # Ignore errors if the canvas isn't ready
    
    def load_historical_data(self):
        """Load all historical data files for playback - in background thread"""
        # Start in a separate thread to avoid blocking the UI
        self.thread_pool.submit(self._load_historical_data_thread)
    
    def _load_historical_data_thread(self):
        """Background thread implementation of historical data loading"""
        try:
            # Enable playback mode to prevent connection attempts
            self.playback_mode = True
            
            # Find all CSV files in the data directory
            csv_files = glob.glob(f"{self.data_dir}/*.csv")
            
            if not csv_files:
                logger.warning("No historical data files found for playback")
                self.update_playback_status("No data available")
                return
            
            # Sort files chronologically
            csv_files.sort()
            
            # Initialize lists for the combined data
            all_times = []
            all_prices = []
            all_volumes = []
            
            # Show loading status
            self.update_playback_status(f"Loading {len(csv_files)} files...")
            
            # Load and combine all data files
            loaded_files = 0
            for file in csv_files:
                try:
                    df = pd.read_csv(file)
                    df['time'] = pd.to_datetime(df['time'])
                    
                    # Add to our data lists
                    all_times.extend(df['time'].tolist())
                    all_prices.extend(df['price'].tolist())
                    
                    # Handle volume deltas
                    if 'volume_delta' in df.columns:
                        all_volumes.extend(df['volume_delta'].tolist())
                    else:
                        vol_deltas = df['volume'].diff().fillna(0).tolist()
                        all_volumes.extend(vol_deltas)
                    
                    loaded_files += 1
                    # Update status periodically
                    if loaded_files % 5 == 0:
                        self.update_playback_status(f"Loading files: {loaded_files}/{len(csv_files)}")
                        # Give UI time to update
                        time.sleep(0.05)
                        
                except Exception as e:
                    logger.error(f"Error loading file {file}: {e}")
                    continue
            
            # Sort the combined data chronologically
            if all_times:
                self.update_playback_status("Sorting data...")
                sorted_data = sorted(zip(all_times, all_prices, all_volumes), key=lambda x: x[0])
                
                # Store as playback data
                self.playback_data = {
                    'times': [item[0] for item in sorted_data],
                    'prices': [item[1] for item in sorted_data],
                    'volumes': [item[2] for item in sorted_data]
                }
                
                # Reset playback position
                self.playback_index = 0
                self.playback_mode = True
                self.playback_paused = True
                
                # Update button state (safely, from main thread)
                self.play_pause_button.label.set_text('Play')
                
                # Clear current display data
                with self.plot_lock:
                    self.times = []
                    self.prices = []
                    self.volumes = []
                
                logger.info(f"Loaded {len(self.playback_data['times'])} historical data points from {loaded_files} files")
                self.update_playback_status(f"Ready to play {len(self.playback_data['times'])} points")
            else:
                logger.warning("No valid data found in CSV files")
                self.update_playback_status("No valid data found")
                
        except Exception as e:
            logger.error(f"Error loading historical data: {e}")
            self.update_playback_status("Error loading data")
    
    async def start(self):
        """Main application loop with proper threading model"""
        # Start UI update loop immediately in the main thread
        ui_update_task = asyncio.create_task(self.update_ui_loop())
        
        # Start connection management in a separate task
        if not self.playback_mode:
            self.connection_task = asyncio.create_task(self.connect_with_retry())
        
        # Wait for both tasks
        await asyncio.gather(ui_update_task, asyncio.create_task(self.keep_alive()))
    
    async def update_ui_loop(self):
        """Dedicated loop for UI updates to ensure responsiveness"""
        while True:
            try:
                # Update plot regardless of connection status
                self.update_plot(None)
                await asyncio.sleep(0.05)  # Update UI frequently for smooth playback
            except Exception as e:
                logger.error(f"Error in UI update loop: {e}")
                await asyncio.sleep(0.5)  # Short wait before retrying UI updates
    
    async def keep_alive(self):
        """Simple keep-alive loop to prevent the application from exiting"""
        while True:
            await asyncio.sleep(1.0)
    
    def update_plot(self, frame):
        """Thread-safe update of the plot"""
        # Need to acquire lock for thread safety
        with self.plot_lock:
            # Check if we're in playback mode and need to advance
            if self.playback_mode and not self.playback_paused:
                data_updated = self.advance_playback()
                if not data_updated:
                    return
                
            try:
                if not self.prices or not self.times:
                    return
                
                # Update price plot
                self.ax1.clear()
                self.ax1.plot(self.times, self.prices, 'b-')
                
                # Plot FFT fit line if available
                if self.fft_fit_line is not None and self.fft_x_pred is not None:
                    # Convert FFT x values (seconds) back to datetime for plotting
                    fft_times = [self.times[0] + timedelta(seconds=s) for s in self.fft_x_pred]
                    self.ax1.plot(fft_times, self.fft_fit_line, 'r--', linewidth=1.5, 
                                 alpha=0.7, label='FFT Fit')
                    self.ax1.legend(loc='upper right')
                
                self.ax1.set_title(f'Live Tick Data: {self.symbol} (Press +/- to zoom, r to reset)')
                self.ax1.set_ylabel('Price')
                self.ax1.grid(True)
                
                # Update bid/ask display
                if self.current_bid is not None and self.current_ask is not None:
                    bid_ask_str = f'Bid: {self.current_bid:.2f}\nAsk: {self.current_ask:.2f}\nSpread: {(self.current_ask - self.current_bid):.2f}'
                    self.ax1.text(1.02, 0.5, bid_ask_str,
                                transform=self.ax1.transAxes,
                                verticalalignment='center',
                                bbox=dict(facecolor='white', alpha=0.8, edgecolor='gray'))
                
                # Update volume plot
                self.ax2.clear()
                
                # Color bars based on price changes
                colors = ['g' if i == 0 or self.prices[i] >= self.prices[i-1] else 'r' 
                         for i in range(len(self.prices))]
                
                # Calculate bar width based on time window
                if len(self.times) > 1:
                    time_range = (self.times[-1] - self.times[0]).total_seconds()
                    bar_width = timedelta(seconds=time_range/100)
                else:
                    bar_width = timedelta(milliseconds=100)
                
                # Plot volumes as bars
                self.ax2.bar(self.times, self.volumes, color=colors, alpha=0.5, 
                            width=bar_width)
                self.ax2.set_ylabel('Volume')
                self.ax2.grid(True)
                
                # Format x-axis for both plots
                for ax in [self.ax1, self.ax2]:
                    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
                    plt.setp(ax.get_xticklabels(), rotation=45)
                
                # Adjust plot limits based on zoom factor and add space for prediction
                if len(self.times) > 1:
                    # Calculate current window size based on zoom factor
                    self.current_window_size = int(self.base_window_size * self.zoom_factor)
                    self.current_window_size = min(self.current_window_size, len(self.times))
                    
                    # Get time range and add 5 seconds of future space
                    recent_times = self.times[-self.current_window_size:]
                    future_time = self.times[-1] + timedelta(seconds=5)
                    
                    # Set x limits for both plots
                    for ax in [self.ax1, self.ax2]:
                        ax.set_xlim(recent_times[0], future_time)
                
                # Set reasonable y-axis limits for volume
                if self.volumes:
                    max_vol = max(self.volumes[-self.current_window_size:] if len(self.volumes) > self.current_window_size else self.volumes)
                    self.ax2.set_ylim(0, max_vol * 1.1)
                
                # Add option info text box
                if self.selected_option_data['strike'] is not None:
                    option_text = (
                        f"ATM Option (Call)\n"
                        f"Strike: {self.selected_option_data['strike']}\n"
                        f"Expiry: {self.selected_option_data['expiry']}\n"
                        f"Bid: {self.selected_option_data['bid']} ({self.selected_option_data['bidSize']})\n"
                        f"Ask: {self.selected_option_data['ask']} ({self.selected_option_data['askSize']})"
                    )
                    self.ax1.text(1.02, 0.1, option_text, transform=self.ax1.transAxes, fontsize=10,
                                  verticalalignment='bottom', bbox=dict(facecolor='white', alpha=0.8, edgecolor='gray'))
                
                # Try non-blocking updates
                self.fig.tight_layout()
                try:
                    self.fig.canvas.draw_idle()
                    self.fig.canvas.flush_events()
                except Exception as e:
                    # If we can't do non-blocking, fall back to regular draw
                    try:
                        self.fig.canvas.draw()
                    except Exception as e2:
                        logger.error(f"Cannot update display: {e2}")
                    
            except Exception as e:
                logger.error(f"Error in update_plot: {e}")
                
    # Keep the event handlers short and offload heavy work to threads
    def on_rewind(self, event):
        if self.playback_data is not None:
            with self.plot_lock:
                self.playback_index = 0
                self.times = []
                self.prices = []
                self.volumes = []
            self.update_playback_status("Rewound to start")
    
    def on_play_pause(self, event):
        with self.ui_event_lock:  # Prevent multiple simultaneous clicks
            if not self.playback_mode or self.playback_data is None:
                self.load_historical_data()
                return
            
            self.playback_paused = not self.playback_paused
            button_text = 'Pause' if not self.playback_paused else 'Play'
            self.play_pause_button.label.set_text(button_text)
            
            status = "Paused" if self.playback_paused else f"Playing at {self.playback_speed}x speed"
            self.update_playback_status(status)
            
            if not self.playback_paused:
                self.playback_last_time = time.time()
    
    def on_load_data(self, event):
        with self.ui_event_lock:
            # Enable playback mode to prevent connection attempts
            self.playback_mode = True
            self.update_playback_status("Loading data...")
            self.load_historical_data()
    
    def on_live_mode(self, event):
        with self.ui_event_lock:
            self.playback_mode = False
            self.update_playback_status("Live Mode")
            
            # Restart connection task if needed
            if self.connection_task is None or self.connection_task.done():
                self.connection_task = asyncio.create_task(self.connect_with_retry())
                logger.info("Restarted connection task for live mode")
    
    def on_speed_change(self, val):
        self.playback_speed = val
        if self.playback_mode and not self.playback_paused:
            self.update_playback_status(f"Playing at {self.playback_speed:.1f}x speed")
    
    def on_key_press(self, event):
        with self.ui_event_lock:
            if event.key == '+':
                self.zoom_factor *= 0.8  # Zoom in
            elif event.key == '-':
                self.zoom_factor *= 1.2  # Zoom out
            elif event.key == 'r':
                self.zoom_factor = 1.0  # Reset zoom
            elif event.key == ' ':  # Spacebar for play/pause
                self.on_play_pause(None)
            elif event.key == 'home':  # Home key for rewind
                self.on_rewind(None)
            elif event.key == 'l':  # 'l' for live mode
                self.on_live_mode(None)
    
    def advance_playback(self):
        """Advance the playback based on the current speed setting"""
        if not self.playback_mode or self.playback_paused or self.playback_data is None:
            return False
        
        if self.playback_index >= len(self.playback_data['times']):
            logger.info("Reached end of playback data")
            self.playback_paused = True
            self.play_pause_button.label.set_text('Play')
            self.update_playback_status("End of data reached")
            return False
        
        # Calculate how many points to advance based on speed and elapsed time
        current_time = time.time()
        if self.playback_last_time is None:
            self.playback_last_time = current_time
            return False
        
        elapsed = current_time - self.playback_last_time
        points_to_advance = max(1, int(elapsed * self.playback_speed * 10))  # Scale factor for smooth playback
        
        # Add data points from the playback data
        end_idx = min(self.playback_index + points_to_advance, len(self.playback_data['times']))
        
        for i in range(self.playback_index, end_idx):
            self.times.append(self.playback_data['times'][i])
            self.prices.append(self.playback_data['prices'][i])
            self.volumes.append(self.playback_data['volumes'][i])
        
        self.playback_index = end_idx
        self.playback_last_time = current_time
        
        # Periodically queue FFT analysis during playback (without blocking)
        if len(self.prices) % 50 == 0:
            self.queue_fft_analysis()
        
        return True

    async def connect_with_retry(self):
        """Connect to Interactive Brokers with retry logic"""
        retry_count = 0
        while retry_count < self.max_retries and not self.playback_mode:
            try:
                if not self.is_connected:
                    logger.info(f"Attempting to connect to IB API (attempt {retry_count + 1}/{self.max_retries})")
                    
                    # Use a timeout to prevent blocking the UI
                    try:
                        await asyncio.wait_for(
                            self.ib.connectAsync('127.0.0.1', 4001, clientId=1),
                            timeout=3.0
                        )
                        self.is_connected = True
                        logger.info("Successfully connected to IB API")
                        self.contract = Stock(self.symbol, self.exchange, self.currency)
                        self.ib.reqMktData(self.contract, '', False, False)
                        self.ib.pendingTickersEvent += self.on_tick
                        logger.info(f"Subscribed to market data for {self.symbol}")
                        retry_count = 0  # Reset retry count on successful connection
                    except asyncio.TimeoutError:
                        logger.error("Connection attempt timed out")
                        self.is_connected = False
                        retry_count += 1
                
                # Short sleep to allow UI to remain responsive
                await asyncio.sleep(0.1)
                
                # If we're connected, just do periodic checks
                if self.is_connected:
                    await asyncio.sleep(1.0)
                # If in playback mode, exit connection attempts
                elif self.playback_mode:
                    logger.info("Playback mode active, stopping connection attempts")
                    break
                # Otherwise, wait before retry
                else:
                    # Update status to show we're retrying
                    if hasattr(self, 'playback_status_text') and not self.playback_mode:
                        self.update_playback_status(f"Connection retry in {self.connection_retry_delay}s")
                    
                    # Use shorter sleep intervals to keep UI responsive
                    for i in range(self.connection_retry_delay * 10):
                        # Check if we've switched to playback mode
                        if self.playback_mode:
                            break
                        await asyncio.sleep(0.1)  # Small sleep to keep UI responsive
                    
            except Exception as e:
                logger.error(f"Connection attempt failed: {e}")
                self.is_connected = False
                retry_count += 1
                
                # Don't retry if in playback mode
                if self.playback_mode:
                    break
        
        if retry_count >= self.max_retries and not self.playback_mode:
            logger.error("Maximum retry attempts reached. Please check your IB Gateway/TWS connection.")
            self.update_playback_status("Connection failed - Using playback mode only")

    def on_tick(self, tickers):
        # Skip if in playback mode
        if self.playback_mode:
            return
            
        try:
            if not self.is_connected:
                return  # Don't process ticks if not connected
                
            for ticker in tickers:
                # Update bid/ask prices
                if hasattr(ticker, 'bid') and ticker.bid:
                    self.current_bid = ticker.bid
                if hasattr(ticker, 'ask') and ticker.ask:
                    self.current_ask = ticker.ask
                
                # --- ATM Option Subscription Logic ---
                if not self.option_subscription_initialized and hasattr(ticker, 'last') and ticker.last:
                    current_price = ticker.last
                    asyncio.create_task(self.subscribe_to_atm_option(current_price))
                    self.option_subscription_initialized = True
                # --- End ATM Option Subscription Logic ---
                
                if hasattr(ticker, 'last') and ticker.last:
                    current_time = datetime.now()
                    
                    # Get current cumulative volume
                    current_volume = ticker.volume if hasattr(ticker, 'volume') and ticker.volume is not None else 0
                    
                    # Initialize volume tracking
                    if not self.initialized:
                        self.last_volume = current_volume
                        self.initialized = True
                        logger.info(f"Initialized volume tracking at {current_volume}")
                        continue
                    
                    # Calculate tick volume
                    tick_volume = max(0, current_volume - self.last_volume)
                    
                    # Only record if there's actual volume and we're initialized
                    if tick_volume > 0:
                        self.prices.append(ticker.last)
                        self.times.append(current_time)
                        self.volumes.append(tick_volume)
                        
                        # Add to current minute's data
                        self.current_minute_data['times'].append(current_time)
                        self.current_minute_data['prices'].append(ticker.last)
                        self.current_minute_data['volumes'].append(tick_volume)
                        
                        # Log updates
                        if len(self.prices) % 10 == 0 or (self.last_update and (current_time - self.last_update).seconds > 5):
                            logger.info(f"Tick: Price={ticker.last}, Time={current_time}, Volume={tick_volume}")
                            self.last_update = current_time
                        
                        # Periodically update FFT analysis
                        if len(self.prices) % 20 == 0:  # Update FFT analysis every 20 ticks
                            self.perform_fft_analysis_threaded(self.prices[-20:], self.times[-20:])
                        
                        # Save data every minute
                        self.save_data()
                        
                        # Keep only recent data for performance
                        if len(self.prices) > 1000:
                            self.prices = self.prices[-1000:]
                            self.times = self.times[-1000:]
                            self.volumes = self.volumes[-1000:]
                        
                        # Update the plot
                        self.update_plot(None)
                    
                    # Update last volume
                    self.last_volume = current_volume
                    
        except Exception as e:
            logger.error(f"Error in on_tick: {e}")

    def save_data(self):
        try:
            current_time = datetime.now()
            current_minute = current_time.replace(second=0, microsecond=0)
            
            # If we have data and it's a new minute, save the data
            if self.prices and (
                not self.last_save or 
                current_minute > self.last_save.replace(second=0, microsecond=0)
            ):
                # Create filename with minute timestamp
                filename = f"{self.data_dir}/{self.symbol}_{current_minute.strftime('%Y%m%d_%H%M')}.csv"
                
                # Calculate volume deltas for storage if not already done
                volumes_for_storage = self.volumes
                
                df = pd.DataFrame({
                    'time': [t.isoformat() for t in self.times],
                    'price': self.prices,
                    'volume': volumes_for_storage,
                    'volume_delta': volumes_for_storage  # Store deltas directly for easier bootstrapping
                })
                df.to_csv(filename, index=False)
                logger.info(f"Saved {len(self.prices)} data points to {filename}")
            
            # Update last save time
            self.last_save = current_time
            
        except Exception as e:
            logger.error(f"Error saving data: {e}")

    async def subscribe_to_atm_option(self, current_price):
        # Request option chain params
        params = await self.ib.reqSecDefOptParamsAsync(self.symbol, '', self.symbol, 'STK')
        if not params:
            logger.error('No option chain params returned')
            return
        chain = params[0]
        strikes = sorted(chain.strikes)
        expiry = sorted(chain.expirations)[0]  # Use nearest expiry for now
        # Find closest strike to current price
        closest_strike = min(strikes, key=lambda x: abs(x - current_price))
        self.selected_option_data['strike'] = closest_strike
        self.selected_option_data['expiry'] = expiry
        self.selected_option_data['right'] = 'C'  # For now, just call option
        # Create Option contract
        option_contract = Option(self.symbol, expiry, closest_strike, 'C', self.exchange, currency=self.currency)
        self.selected_option_contract = option_contract
        # Subscribe to market data for this option
        self.ib.reqMktData(option_contract, '', False, False)
        self.ib.pendingTickersEvent += self.on_option_tick
        logger.info(f"Subscribed to ATM option: {self.symbol} {expiry} {closest_strike}C")

    def on_option_tick(self, tickers):
        # Only update for our selected contract
        for ticker in tickers:
            if hasattr(ticker, 'contract') and self.selected_option_contract and ticker.contract.conId == self.selected_option_contract.conId:
                self.selected_option_data['bid'] = getattr(ticker, 'bid', None)
                self.selected_option_data['ask'] = getattr(ticker, 'ask', None)
                self.selected_option_data['bidSize'] = getattr(ticker, 'bidSize', None)
                self.selected_option_data['askSize'] = getattr(ticker, 'askSize', None)

async def main():
    try:
        plotter = LiveTickPlotter('QQQ')
        await plotter.start()
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script terminated by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}") 