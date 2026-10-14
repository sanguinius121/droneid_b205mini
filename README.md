# DroneID receiver + local web

Bản bàn giao độc lập bộ thu DroneID UHD/B205mini.

- `receiver/`: bộ thu, detector và decoder.
- `web/`: bản đồ, trace và bản tin JSON của riêng bộ thu.
- `native/turbofec/`: backend Turbo C, biên dịch tại máy đích.
- `scripts/run_receiver.py`: lệnh chuẩn với cấu hình đã thử gần nhất.
- [BRIEF_MAY_MOI.md](BRIEF_MAY_MOI.md): hướng dẫn cài đặt, chạy, cấu hình, bằng chứng và giới hạn.
- [VALIDATION.md](VALIDATION.md): kiểm tra gói bàn giao.

Không kèm IQ, log thực nghiệm, firmware E200 hoặc bộ thu tham chiếu UART.
