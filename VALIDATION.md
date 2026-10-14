# Kiểm tra bản bàn giao — 14/10/2026

- Biên dịch lại TurboFEC từ mã nguồn đi kèm bằng GCC: đạt.
- 54 unit tests: đạt; gồm scheduler, dự đoán tạm thời, QPSK, Turbo/CRC và đọc dữ liệu web. Không cần SDR hoặc corpus bên ngoài.
- Clone sạch sang `/tmp/droneid-handoff-clean-check`, build TurboFEC và chạy lại 54 tests: đạt; không cần thư mục dự án gốc.
- Receiver wrapper `--help`: đạt, không mở phần cứng.
- Python compileall và JavaScript syntax check: đạt.
- HTTP GET `/` trả 200; GET `/api/state` trả idle và 0 bản tin, không khởi động RF.
- Chromium headless tải trang và chạy JavaScript: hiển thị idle, CRC đúng 0, điểm tọa độ 0.
- Chưa kiểm thử marker/trace bằng đường bay live trong đợt đóng gói; chưa thu RF trên máy đích.

Máy phát triển có cảnh báo Matplotlib Axes3D do nhiều bản cài đặt; không làm thất bại 54 tests. Môi trường mới nên dùng virtualenv theo BRIEF_MAY_MOI.md.

Thư viện `.so`, dữ liệu IQ và các file chạy thử không được đưa vào Git. Xem requirements-tested.txt để biết các phiên bản Python packages đã dùng cho kiểm tra này.
