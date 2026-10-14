# DroneID receiver + local web

Bản bàn giao độc lập bộ thu DroneID UHD/B205mini.

- `receiver/`: bộ thu, detector và decoder.
- `web/`: bản đồ, trace và bản tin JSON của riêng bộ thu.
- [`comparison/`](comparison/README.md): web đối chứng ba nguồn B205mini + E200 (TCP CSV) + UART (115200), cấu hình kết nối và replay.
- `native/turbofec/`: backend Turbo C, biên dịch tại máy đích.
- `scripts/run_receiver.py`: lệnh chuẩn với cấu hình đã thử gần nhất.
- [BRIEF_MAY_MOI.md](BRIEF_MAY_MOI.md): hướng dẫn cài đặt, chạy, cấu hình, bằng chứng và giới hạn.
- [VALIDATION.md](VALIDATION.md): kiểm tra gói bàn giao.

Không kèm IQ, log thực nghiệm hoặc firmware E200. Có collector kết nối E200/UART để so sánh đầu ra.
