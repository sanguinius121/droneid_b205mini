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

## Bổ sung chế độ đối chứng ba nguồn

- Tổng 61 kiểm thử đạt, gồm 54 tests trước và 7 tests so sánh mới.
- Collector E200 được chạy thật bằng subprocess với TCP loopback và CSV phân mảnh có heartbeat: ghi đúng một bản tin, giữ CRC unknown và byte thô.
- Collector UART được chạy thật bằng subprocess với pseudo-terminal (cổng serial giả lập): ghi đúng message, CRC flag và tọa độ theo thứ tự cấu hình.
- Kiểm tra cửa sổ so sánh chung, bản ghi JSONL chưa đủ dòng và phân biệt bằng chứng CRC: đạt.
- Luồng điều phối ba nguồn với tiến trình giả: kiểm tra truyền config IP/UART, gọi wrapper receiver hiện tại, lưu artifacts và trạng thái dừng thành công.
- Chromium headless tải web đối chứng: trạng thái CHƯA THU, đủ ba nhóm E200/UART/bộ thu của chúng ta.
- Không dùng dữ liệu bay thật và không thu live phần cứng trong lần bổ sung này.
