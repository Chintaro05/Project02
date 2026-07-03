import socket
import struct
import time
import threading
import queue
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import io

# Multicast configuration
MULTICAST_GROUP = "239.1.1.1"
PORT = 5004
MAX_PACKET_SIZE = 65536  # UDP max payload size is 65507, 65536 is safe

class MulticastClientGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Multicast Video Player")
        self.root.configure(bg="#1e222b")
        
        # Center the window
        width, height = 700, 400
        ws = self.root.winfo_screenwidth()
        hs = self.root.winfo_screenheight()
        x = (ws // 2) - (width // 2)
        y = (hs // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.resizable(False, False)
        
        # Connection and state variables
        self.running = True
        self.last_packet_time = 0
        
        # Statistics variables
        self.total_packets_received = 0
        self.total_packets_lost = 0
        self.last_seq_num = None
        self.total_bytes_received = 0
        
        # Dynamic stats (for last 1-second interval)
        self.interval_packets = 0
        self.interval_bytes = 0
        self.current_fps = 0.0
        self.current_bitrate = 0.0  # kbps
        
        # Latency tracking (sliding window of last 20 frames)
        self.latencies = []
        
        # Thread-safe queue for JPEGs and packets from network thread
        self.frame_queue = queue.Queue()
        
        # Dictionary to store active reassembly of frame fragments (frame_num -> frame_info)
        self.active_frames = {}
        
        # Initialize UI Components
        self.setup_ui()
        
        # Setup Multicast Socket
        self.setup_socket()
        
        # Start receiver thread
        self.recv_thread = threading.Thread(target=self.receive_packets, daemon=True)
        self.recv_thread.start()
        
        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Start periodic GUI updates
        self.poll_frame_queue()
        self.update_periodic_stats()
        
    def setup_ui(self):
        # Left Panel (Video Display)
        self.video_frame = tk.Frame(self.root, bg="#11141a", width=420, height=320, bd=2, relief=tk.SOLID)
        self.video_frame.pack_propagate(False)
        self.video_frame.place(x=15, y=20)
        
        # Video placeholder/label
        self.video_label = tk.Label(self.video_frame, bg="#11141a")
        self.video_label.pack(expand=True, fill=tk.BOTH)
        
        # Placeholder image/text
        self.show_placeholder_text("Waiting for stream...")
        
        # Status Bar (below video)
        self.status_frame = tk.Frame(self.root, bg="#1e222b")
        self.status_frame.place(x=15, y=350, width=420, height=30)
        
        self.status_dot = tk.Label(self.status_frame, text="●", fg="#f87171", bg="#1e222b", font=("Segoe UI", 12, "bold"))
        self.status_dot.pack(side=tk.LEFT)
        
        self.status_text = tk.Label(self.status_frame, text="WAITING FOR STREAM", fg="#abb2bf", bg="#1e222b", font=("Segoe UI", 10, "bold"))
        self.status_text.pack(side=tk.LEFT, padx=5)
        
        # Right Panel (Dashboard / Stats)
        self.dashboard_frame = tk.Frame(self.root, bg="#282c34", width=230, height=320, bd=0, relief=tk.FLAT)
        self.dashboard_frame.pack_propagate(False)
        self.dashboard_frame.place(x=455, y=20)
        
        # Dashboard Title
        self.title_label = tk.Label(self.dashboard_frame, text="STREAM DASHBOARD", fg="#ffffff", bg="#282c34", font=("Segoe UI", 11, "bold"))
        self.title_label.pack(pady=(15, 5))
        
        # Group Label
        self.group_label = tk.Label(self.dashboard_frame, text=f"IP: {MULTICAST_GROUP}:{PORT}", fg="#51afef", bg="#282c34", font=("Segoe UI", 9, "bold"))
        self.group_label.pack(pady=(0, 10))
        
        # Stats Grid Container
        self.stats_container = tk.Frame(self.dashboard_frame, bg="#282c34")
        self.stats_container.pack(fill=tk.BOTH, expand=True, padx=15)
        
        # Labels helper functions
        def add_stat_row(parent, row, label_text):
            lbl = tk.Label(parent, text=label_text, fg="#abb2bf", bg="#282c34", font=("Segoe UI", 9), anchor="w")
            lbl.grid(row=row, column=0, sticky="w", pady=4)
            val = tk.Label(parent, text="--", fg="#ffffff", bg="#282c34", font=("Segoe UI", 9, "bold"), anchor="e")
            val.grid(row=row, column=1, sticky="e", pady=4)
            parent.grid_columnconfigure(0, weight=1)
            parent.grid_columnconfigure(1, weight=1)
            return val

        self.val_packets = add_stat_row(self.stats_container, 0, "Packets Recv:")
        self.val_lost = add_stat_row(self.stats_container, 1, "Packets Lost:")
        self.val_loss_rate = add_stat_row(self.stats_container, 2, "Loss Rate:")
        self.val_fps = add_stat_row(self.stats_container, 3, "Framerate:")
        self.val_latency = add_stat_row(self.stats_container, 4, "Latency:")
        self.val_bitrate = add_stat_row(self.stats_container, 5, "Bitrate:")
        self.val_data = add_stat_row(self.stats_container, 6, "Total Data:")
        
        # Button Panel (Bottom Right)
        self.btn_frame = tk.Frame(self.root, bg="#1e222b")
        self.btn_frame.place(x=455, y=350, width=230, height=35)
        
        # Reset Stats Button
        self.reset_btn = tk.Button(self.btn_frame, text="Reset Stats", command=self.reset_statistics, 
                                   bg="#3b4048", fg="#ffffff", activebackground="#4b5263", activeforeground="#ffffff",
                                   font=("Segoe UI", 9, "bold"), bd=0, cursor="hand2", width=11)
        self.reset_btn.pack(side=tk.LEFT)
        
        # Exit Button
        self.exit_btn = tk.Button(self.btn_frame, text="Exit Player", command=self.on_close, 
                                  bg="#e06c75", fg="#ffffff", activebackground="#be5046", activeforeground="#ffffff",
                                  font=("Segoe UI", 9, "bold"), bd=0, cursor="hand2", width=11)
        self.exit_btn.pack(side=tk.RIGHT)
        
    def show_placeholder_text(self, text):
        self.video_label.config(image="")
        self.video_label.config(text=text, fg="#5c6370", font=("Segoe UI", 14, "italic"))
        
    def setup_socket(self):
        """
        Creates and configures the UDP multicast socket.
        """
        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            
            # Allow multiple clients to bind to the same port on the same machine
            self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            # Bind to port 5004. Binding to empty string ('') or '0.0.0.0' works on all platforms.
            self.client_socket.bind(('', PORT))
            
            # Pack membership request structure
            mreq = struct.pack('4sL', socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
            
            # Join the multicast group
            self.client_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            
            # Set a socket timeout to check for the 'running' flag periodically
            self.client_socket.settimeout(0.5)
            
            print(f"[Client] Bound and joined multicast group {MULTICAST_GROUP}:{PORT}")
        except Exception as e:
            messagebox.showerror("Socket Error", f"Failed to initialize multicast socket:\n{e}")
            sys.exit(1)
            
    def receive_packets(self):
        """
        Runs on background thread. Receives incoming multicast packets, processes headers,
        and reassembles fragments into complete JPEG video frames.
        """
        while self.running:
            try:
                # Receive packet from multicast socket
                packet, addr = self.client_socket.recvfrom(MAX_PACKET_SIZE)
                
                now = time.time()
                self.last_packet_time = now
                
                # Check header size (24 bytes for fragmented packets)
                header_size = struct.calcsize("!IdIHHI")
                if len(packet) < header_size:
                    continue
                    
                header = packet[:header_size]
                payload = packet[header_size:]
                
                # Unpack header
                seq_num, timestamp, frame_num, frag_idx, total_fragments, payload_length = struct.unpack("!IdIHHI", header)
                
                # Sanity check payload size matches packet content
                if len(payload) < payload_length:
                    continue
                frag_data = payload[:payload_length]
                
                # Packet loss detection (network-level sequence number)
                if self.last_seq_num is not None:
                    if seq_num > self.last_seq_num + 1:
                        lost = seq_num - self.last_seq_num - 1
                        self.total_packets_lost += lost
                    elif seq_num < self.last_seq_num:
                        # Out-of-order packet or server restarted
                        if self.last_seq_num - seq_num > 1000:
                            self.last_seq_num = None
                
                if self.last_seq_num is None or seq_num > self.last_seq_num:
                    self.last_seq_num = seq_num
                    
                self.total_packets_received += 1
                self.total_bytes_received += len(packet)
                self.interval_packets += 1
                self.interval_bytes += len(packet)
                
                # Record latency
                latency = max(0.0, now - timestamp) * 1000.0  # in ms
                self.latencies.append(latency)
                if len(self.latencies) > 20:
                    self.latencies.pop(0)
                    
                # Reassemble fragments into frames
                if frame_num not in self.active_frames:
                    self.active_frames[frame_num] = {
                        'timestamp': timestamp,
                        'total_fragments': total_fragments,
                        'fragments': {},
                        'creation_time': now
                    }
                    
                self.active_frames[frame_num]['fragments'][frag_idx] = frag_data
                
                # Check if we have all fragments for this frame
                frame_info = self.active_frames[frame_num]
                if len(frame_info['fragments']) == frame_info['total_fragments']:
                    # Reassemble frame
                    assembled_jpeg = b"".join(frame_info['fragments'][i] for i in range(frame_info['total_fragments']))
                    
                    # Place JPEG frame data and info into queue for the GUI thread
                    self.frame_queue.put((assembled_jpeg, seq_num, frame_num))
                    
                    # Remove from active assembly dictionary
                    del self.active_frames[frame_num]
                    
                # Cleanup stale reassemblies (older than 2.0s or k < current - 10) to prevent memory leak
                current_keys = list(self.active_frames.keys())
                for k in current_keys:
                    if now - self.active_frames[k]['creation_time'] > 2.0 or k < frame_num - 10:
                        del self.active_frames[k]
                        
            except socket.timeout:
                # Normal behavior when server is not streaming.
                continue
            except Exception as e:
                if self.running:
                    print(f"[Client Network Thread] Error: {e}")
                    time.sleep(0.1)
                    
    def poll_frame_queue(self):
        """
        Polls the queue for new video frames and renders the latest one.
        Runs on GUI thread.
        """
        if not self.running:
            return
            
        try:
            # Drain queue to only show the most recent frame (prevents video lag)
            latest_frame = None
            try:
                while True:
                    latest_frame = self.frame_queue.get_nowait()
            except queue.Empty:
                pass
                
            if latest_frame:
                jpeg_data, seq_num, frame_num = latest_frame
                
                # Convert JPEG bytes to PIL image and then to PhotoImage
                try:
                    img = Image.open(io.BytesIO(jpeg_data))
                    photo = ImageTk.PhotoImage(image=img)
                    
                    # Update label in Tkinter
                    self.video_label.config(image=photo, text="")
                    self.video_label.image = photo  # Keep a reference!
                except Exception as img_err:
                    print(f"[Client GUI] Error decoding image: {img_err}")
                    
        except Exception as e:
            print(f"[Client GUI] Error in poll_frame_queue: {e}")
        finally:
            # Schedule next check in 10 ms
            self.root.after(10, self.poll_frame_queue)
            
    def update_periodic_stats(self):
        """
        Updates statistics on the GUI. Runs every 1 second on the GUI thread.
        """
        if not self.running:
            return
            
        now = time.time()
        time_since_packet = now - self.last_packet_time
        
        # Check active status
        if self.last_packet_time > 0 and time_since_packet < 2.0:
            self.status_dot.config(fg="#34d399") # Green
            self.status_text.config(text="STREAMING ACTIVE", fg="#34d399")
        else:
            self.status_dot.config(fg="#f87171") # Red
            self.status_text.config(text="WAITING FOR STREAM / OFFLINE", fg="#f87171")
            self.current_fps = 0.0
            self.current_bitrate = 0.0
            self.show_placeholder_text("Waiting for stream...")
            
        # Calculate dynamic stats for this period
        if self.interval_packets > 0:
            self.current_fps = self.interval_packets / 1.0  # since interval is 1s
            self.current_bitrate = (self.interval_bytes * 8.0) / 1024.0  # kbps
            
            # Reset counters
            self.interval_packets = 0
            self.interval_bytes = 0
            
        # Compute avg latency
        avg_latency = sum(self.latencies) / len(self.latencies) if self.latencies else 0.0
        
        # Loss rate calculation
        total_attempts = self.total_packets_received + self.total_packets_lost
        loss_rate = (self.total_packets_lost / total_attempts * 100.0) if total_attempts > 0 else 0.0
        
        # Total data received in MB
        total_mb = self.total_bytes_received / (1024.0 * 1024.0)
        
        # Update widgets text
        self.val_packets.config(text=f"{self.total_packets_received}")
        self.val_lost.config(text=f"{self.total_packets_lost}")
        
        # Highlight loss rate if > 0%
        if loss_rate > 0:
            self.val_loss_rate.config(text=f"{loss_rate:.2f}%", fg="#f87171")
        else:
            self.val_loss_rate.config(text="0.00%", fg="#34d399")
            
        self.val_fps.config(text=f"{self.current_fps:.1f} FPS")
        self.val_latency.config(text=f"{avg_latency:.1f} ms" if self.last_packet_time > 0 and time_since_packet < 2.0 else "--")
        self.val_bitrate.config(text=f"{self.current_bitrate:.1f} kbps")
        self.val_data.config(text=f"{total_mb:.2f} MB")
        
        # Schedule next update in 1 second
        self.root.after(1000, self.update_periodic_stats)
        
    def reset_statistics(self):
        """
        Resets all counters.
        """
        self.total_packets_received = 0
        self.total_packets_lost = 0
        self.last_seq_num = None
        self.total_bytes_received = 0
        self.interval_packets = 0
        self.interval_bytes = 0
        self.latencies.clear()
        print("[Client] Statistics reset by user.")
        
    def on_close(self):
        """
        Exits the GUI cleanly and closes the multicast socket.
        """
        if messagebox.askokcancel("Exit Player", "Do you want to close the video player?"):
            self.running = False
            
            # Clean up socket and leave multicast group
            try:
                mreq = struct.pack('4sL', socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
                self.client_socket.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
                self.client_socket.close()
                print("[Client] Left multicast group and closed socket.")
            except Exception as e:
                print(f"[Client Cleanup] Socket cleanup error: {e}")
                
            self.root.destroy()
            print("[Client] Player interface destroyed. Goodbye!")

def main():
    root = tk.Tk()
    app = MulticastClientGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
