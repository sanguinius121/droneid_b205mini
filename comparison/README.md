# Web đối chứng B205mini + AntSDR E200 + UART

Chế độ này bổ sung cho `web/server.py` (web một nguồn). Nó thu ba đầu ra đồng thời, lưu dữ liệu riêng từng nguồn và hiển thị marker, trace, số message, số điểm GPS, số vị trí khác nhau và message/s trong cùng cửa sổ thời gian. Không chạy cả hai web ở chế độ thu trên cùng một B205mini.

## Cài đặt và kết nối

Từ thư mục gốc repository, dùng môi trường theo BRIEF_MAY_MOI.md và cài lại dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config/comparison.example.json config/comparison.local.json
```

Sửa `config/comparison.local.json` cho máy mới:

```json
{
  "e200_listen_ip": "172.31.100.1",
  "e200_listen_port": 52002,
  "e200_peer_ip": "172.31.100.2",
  "uart_ports": ["/dev/ttyUSB0", "/dev/ttyUSB1"],
  "uart_baudrate": 115200
}
```

**E200:** kết nối Ethernet và cấp nguồn, dùng image đã có sẵn trên thiết bị. Máy tính cần có địa chỉ `e200_listen_ip` trên card Ethernet nối E200; E200 có IP `e200_peer_ip`. Firmware phải được cấu hình gửi TCP về địa chỉ máy tính và cổng `52002` tương ứng. Collector là TCP server nhận CSV `dji_O,...`; nó không kết nối kiểu legacy tới cổng 41030 và không đọc ZMQ 4221/4224. Chỉ thay listen IP trong JSON không tự thay cấu hình IP của máy tính hoặc đích gửi trong firmware. Firewall cần cho phép TCP từ E200 tới cổng này. Không có firmware, image hoặc công cụ flash trong gói Git.

**UART:** kết nối bộ chuyển USB serial và đặt `uart_ports` đúng các cổng đang có. Có thể chỉ khai báo một cổng nếu thiết bị chỉ xuất bản tin ở cổng đó; mặc định hai cổng phải đều mở được, nếu thiếu sẽ báo lỗi và không bắt đầu SDR. Nên dùng đường dẫn `/dev/serial/by-id/...` ổn định nếu có. Collector thu 115200 baud, 8N1, không điều khiển luồng, DTR/RTS đặt thấp và mở exclusive để tránh hai chương trình đọc cùng cổng. User cần có quyền truy cập serial, thường là nhóm `dialout` trên Ubuntu (cần đăng nhập lại sau khi thêm nhóm). Định dạng đã hỗ trợ là dòng `CRC flag 0/1` và `num=..., device=..., serial=..., model=..., drone_GPS=..., freq=..., distance=...km,`. Không giả định mọi log UART là một message DroneID.

**B205mini:** USB 3, antenna RX2, UHD nhận được thiết bị. Web đối chứng gọi `scripts/run_receiver.py`, vì vậy dùng đúng CPU + async + hybrid + center-first + predictor-control, khối 0.175 s, 20 MS/s, gain 30 dB và 8 kênh hiện hành. Không quay về cấu hình receiver cũ của web nghiên cứu.

## Chạy

```bash
python comparison/server.py --config config/comparison.local.json --port 8089
```

Mở http://127.0.0.1:8089. Việc mở web chưa thu RF. Khi drone, controller và ba bộ thu đều sẵn sàng, chọn thời lượng, chọn thứ tự GPS UART rồi bấm **Bắt đầu thu**. Thứ tự UART mặc định là kinh độ rồi vĩ độ, nhưng cần xác minh trên thiết bị thực tế; đổi dropdown nếu firmware xuất ngược lại. Bấm **Dừng & lưu** và chờ trạng thái đã lưu. Khi đóng server, server gửi yêu cầu dừng các tiến trình mà nó tạo.

Collector E200 và UART khởi động trước, sau đó mới khởi động SDR. `ready.json` xác nhận collector đã bind TCP/mở UART, **không xác nhận thiết bị đang phát bản tin**. Nếu collector lỗi, xem `error` trên web và log tương ứng; hệ thống dừng các tiến trình còn lại.

## Đọc số liệu đúng

- E200: mỗi CSV parse được là một message, CRC **chưa xác nhận**. Tọa độ hợp lệ không chứng minh CRC đúng.
- UART: giữ từng message kèm cờ CRC do thiết bị báo, gồm cả flag 0 và chưa rõ. Không vẽ vị trí của message mang flag 0.
- B205mini: chỉ message CRC đúng mới vào nhóm bản tin trên bản đồ; candidate và ZC được đếm riêng.
- Message/s trên web = số message của từng nguồn trong cửa sổ chung chia độ dài cửa sổ. Mẫu số bắt đầu ở lượt SDR nhận đầu tiên và kết thúc ở thời điểm receive/decode cuối của phiên, có thể bao gồm thời gian xả decoder. Các bản ghi tham chiếu ngoài cửa sổ vẫn nằm trong log thô, nhưng không vào bộ đếm hiển thị.
- Khi lọc serial, bộ đếm/tốc độ message và điểm GPS cũng lọc theo serial; bộ đếm candidate/ZC của B205mini vẫn là tổng phiên.
- Số message E200/UART không tương đương số CRC đúng của B205mini. Nếu cùng message xuất hiện ở cả hai UART, nó được đếm hai lần theo đầu ra; chưa khử trùng lặp giữa hai cổng hoặc ba thiết bị.
- Điểm GPS là số bản ghi có tọa độ trong miền hợp lệ, khác (0,0), không mang cờ CRC sai; không phải số vị trí duy nhất. Trace tách theo nguồn và serial, ngắt khi cách nhau hơn 15 giây. Không kèm sửa tọa độ đặc thù các phiên nghiên cứu cũ.

## File lưu và replay

Mỗi phiên nằm tại `sessions/map_<thời gian>_<id>/`:

- `receiver.fc32` cùng `.records.jsonl`, `.events.jsonl`, `.decoded.jsonl`, `.summary.json`, `.shadow.jsonl`.
- `e200/tcp.bin`, `chunks.jsonl`, `messages.jsonl`, `ready.json`, `summary.json`.
- `uart/<tên-cổng>.bin`, `chunks.jsonl`, `messages.jsonl`, `ready.json`, `summary.json`.
- `normalized.jsonl`, `map_summary.json`, `map_config.json`, `processes.json` và log từng tiến trình.

`receiver.fc32` vẫn là IQ candidate đã ghép, không phải raw liên tục. JSON/CSV tải từ web là đầu ra chuẩn hóa trong cửa sổ chung, có áp dụng lọc serial nếu đang chọn.

Mở lại phiên trên web mà không khởi động phần cứng:

```bash
python comparison/server.py --replay sessions/map_YYYYMMDD_HHMMSS_id --port 8089
```

Replay cần giữ nguyên thư mục phiên với artifacts của cả ba nguồn. Không kèm phiên bay thật trong Git. Kết nối TCP loopback, UART giả lập và logic cửa sổ được kiểm thử offline; chưa thu live ba thiết bị trong đợt đóng gói này.
