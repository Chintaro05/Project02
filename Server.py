import sys
import os
import socket
import struct
import time

# Multicast configuration
MULTICAST_GROUP = "239.1.1.1"
PORT = 5004

class MJPEGReader:
    """
    Helper class to read MJPEG files.
    Supports two formats (auto-detected):
    1. Kurose-Ross format: Each frame has a 5-byte ASCII size prefix.
    2. Raw MJPEG format: Concatenated JPEG frames, starting with SOI (\xff\xd8) and ending with EOI (\xff\xd9).
    """
    def __init__(self, filepath):
        self.filepath = filepath
        self.file = None
        self.frame_num = 0
        self.format_mode = None  # "kurose" or "raw"
        self.buffer = bytearray()  # Used for buffering raw MJPEG bytes
        self.open_file()
        
    def open_file(self):
        if self.file:
            self.file.close()
        try:
            self.file = open(self.filepath, 'rb')
            self.frame_num = 0
            self.buffer.clear()
            
            # Read first 5 bytes to auto-detect format
            header_sample = self.file.read(5)
            if not header_sample:
                raise Exception("Empty file")
                
            # Seek back to start of file
            self.file.seek(0)
            
            # Detect JPEG Start of Image (SOI) magic bytes
            if header_sample.startswith(b'\xff\xd8'):
                self.format_mode = "raw"
                print(f"[Server] Detected format: Raw MJPEG stream (concatenated JPEGs)")
            else:
                # Check if it starts with an ASCII decimal size prefix (Kurose-Ross)
                try:
                    int(header_sample.decode('ascii'))
                    self.format_mode = "kurose"
                    print(f"[Server] Detected format: Kurose-Ross custom MJPEG (5-byte ASCII size header)")
                except ValueError:
                    # Fallback to raw parsing if it's not a valid number
                    self.format_mode = "raw"
                    print(f"[Server] Unknown format prefix. Defaulting to Raw MJPEG marker search.")
        except FileNotFoundError:
            print(f"Error: File not found at '{self.filepath}'")
            sys.exit(1)
        except Exception as e:
            print(f"Error opening file '{self.filepath}': {e}")
            sys.exit(1)
        
    def next_frame(self):
        """
        Reads the next JPEG frame from the file based on the detected format.
        Loops back to the beginning of the file if EOF is reached.
        """
        if self.format_mode == "kurose":
            return self.next_frame_kurose()
        else:
            return self.next_frame_raw()
            
    def next_frame_kurose(self):
        try:
            header_bytes = self.file.read(5)
            # If EOF is reached
            if not header_bytes or len(header_bytes) < 5:
                print("\n[Server] Reached EOF. Looping video...")
                self.open_file()
                header_bytes = self.file.read(5)
                if not header_bytes or len(header_bytes) < 5:
                    return None, 0
            
            try:
                frame_length = int(header_bytes.decode('ascii'))
            except ValueError:
                print(f"\n[Server] Error: Invalid frame header '{header_bytes}'. Is this a valid MJPEG file?")
                self.open_file()
                return None, 0
                
            frame_data = self.file.read(frame_length)
            if len(frame_data) < frame_length:
                print("\n[Server] Warning: Incomplete frame read. Looping...")
                self.open_file()
                return None, 0
                
            self.frame_num += 1
            return frame_data, self.frame_num
        except Exception as e:
            print(f"\n[Server] Error reading Kurose frame: {e}")
            return None, 0
            
    def next_frame_raw(self):
        """
        Reads next raw JPEG frame from the concatenated stream.
        Scans for SOI (\xff\xd8) and EOI (\xff\xd9) in-memory using an efficient buffer.
        """
        try:
            chunk_size = 8192
            while True:
                # Find the Start of Image (SOI) marker
                soi_idx = self.buffer.find(b'\xff\xd8')
                if soi_idx != -1:
                    # Find the End of Image (EOI) marker after SOI
                    eoi_idx = self.buffer.find(b'\xff\xd9', soi_idx + 2)
                    if eoi_idx != -1:
                        # Extract the full JPEG frame
                        frame_data = self.buffer[soi_idx : eoi_idx + 2]
                        # Retain leftover bytes in buffer for next frame
                        self.buffer = self.buffer[eoi_idx + 2:]
                        self.frame_num += 1
                        return bytes(frame_data), self.frame_num
                        
                # If we don't have a complete frame, read more from the file
                chunk = self.file.read(chunk_size)
                if not chunk:
                    # End of file reached
                    print("\n[Server] Reached EOF. Looping video...")
                    self.open_file()
                    continue
                self.buffer.extend(chunk)
        except Exception as e:
            print(f"\n[Server] Error reading Raw frame: {e}")
            return None, 0
            
    def close(self):
        if self.file:
            self.file.close()

def main():
    if len(sys.argv) < 2:
        print("Usage: python Server.py <file MJPEG>")
        print("Example: python Server.py movie.mjpeg")
        sys.exit(1)
        
    video_path = sys.argv[1]
    if not os.path.exists(video_path):
        print(f"Error: The file '{video_path}' does not exist.")
        sys.exit(1)
        
    # Initialize the reader
    reader = MJPEGReader(video_path)
    
    # Create UDP Multicast socket
    # AF_INET = IPv4, SOCK_DGRAM = UDP
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    
    # Set Multicast Time-To-Live (TTL)
    # TTL = 1 limits packets to local network segment
    ttl = 1
    server_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    
    print("=" * 60)
    print("           MULTICAST VIDEO STREAMING SERVER")
    print("=" * 60)
    print(f"Streaming File:     {video_path}")
    print(f"Multicast IP:      {MULTICAST_GROUP}")
    print(f"Port:              {PORT}")
    print("Press Ctrl+C to stop streaming.")
    print("=" * 60)
    
    seq_num = 0
    target_fps = 20
    frame_interval = 1.0 / target_fps  # 0.050 seconds (50 ms)
    
    # Track statistics for server-side log
    start_time = time.time()
    last_stat_time = time.time()
    packets_in_period = 0
    frames_in_period = 0
    bytes_in_period = 0
    
    try:
        while True:
            frame_start = time.time()
            
            # Read next frame
            jpeg_data, frame_num = reader.next_frame()
            if jpeg_data is None:
                # Failed to read or reset, retry next loop
                time.sleep(0.01)
                continue
                
            # Split frame into fragments if it exceeds the maximum size
            MAX_FRAGMENT_SIZE = 60000
            total_fragments = (len(jpeg_data) + MAX_FRAGMENT_SIZE - 1) // MAX_FRAGMENT_SIZE
            if total_fragments == 0:
                total_fragments = 1
                
            for frag_idx in range(total_fragments):
                start_offset = frag_idx * MAX_FRAGMENT_SIZE
                end_offset = min(start_offset + MAX_FRAGMENT_SIZE, len(jpeg_data))
                frag_data = jpeg_data[start_offset:end_offset]
                
                seq_num += 1
                timestamp = time.time()
                
                # Packet Format (Custom Header with Fragmentation):
                # ! - Network byte order (big-endian)
                # I - Sequence Number (unsigned 32-bit int, 4 bytes)
                # d - Timestamp (double float, 8 bytes)
                # I - Frame Number (unsigned 32-bit int, 4 bytes)
                # H - Fragment Index (unsigned 16-bit int, 2 bytes)
                # H - Total Fragments (unsigned 16-bit int, 2 bytes)
                # I - Payload Length (unsigned 32-bit int, 4 bytes)
                # Total Header Size = 24 bytes
                header = struct.pack("!IdIHHI", seq_num, timestamp, frame_num, frag_idx, total_fragments, len(frag_data))
                packet = header + frag_data
                
                # Broadcast the packet
                server_socket.sendto(packet, (MULTICAST_GROUP, PORT))
                
                # Update network stats
                packets_in_period += 1
                bytes_in_period += len(packet)
                
            # Update frame count stats
            frames_in_period += 1
            
            # Output periodic log summary (every 2 seconds)
            now = time.time()
            if now - last_stat_time >= 2.0:
                elapsed = now - last_stat_time
                actual_fps = frames_in_period / elapsed
                throughput = (bytes_in_period / 1024.0) / elapsed  # KB/s
                print(f"[Server Log] Sent Seq: {seq_num} | Frame: {frame_num} | Actual FPS: {actual_fps:.2f} | Packets: {packets_in_period} | Throughput: {throughput:.2f} KB/s", end='\r')
                packets_in_period = 0
                frames_in_period = 0
                bytes_in_period = 0
                last_stat_time = now
                
            # Control Frame Rate (Target ~20 FPS)
            frame_elapsed = time.time() - frame_start
            sleep_time = frame_interval - frame_elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
                
    except KeyboardInterrupt:
        print("\n\n[Server] Shutting down...")
    finally:
        reader.close()
        server_socket.close()
        print("[Server] Closed sockets and cleaned up. Goodbye!")

if __name__ == "__main__":
    main()
