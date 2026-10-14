# Bàn giao bộ thu DroneID — 14/10/2026

## Phạm vi

Đây là bản tách mã nguồn từ dự án nghiên cứu DroneSecurity-public_squash, gồm bộ thu UHD/B205mini và web hiển thị kết quả riêng của bộ thu. Không chứa firmware E200, bộ thu UART tham chiếu, dữ liệu IQ, tọa độ thực nghiệm hay các báo cáo so sánh. `receiver/` chứa luồng xử lý và các phụ thuộc nội bộ; một số mô đun cũ được giữ vì entrypoint hiện tại còn nhập chúng. Không chạy các entrypoint cũ thay cho lệnh chuẩn bên dưới.

`receiver/droneid_hybrid.py` là entrypoint phần cứng. `scripts/run_receiver.py` thiết lập cấu hình đã thử gần nhất. `web/server.py` chạy web độc lập và gọi đúng wrapper này. `native/turbofec/` chứa mã C và giấy phép để biên dịch trên máy mới. `config/source_manifest.json` ghi SHA256 mã nguồn gốc trước khi chuyển thư mục; thay đổi đường dẫn thư viện TurboFEC là thay đổi phục vụ đóng gói.

## Môi trường và cài đặt

Máy phát triển: Ubuntu 22.04.5 LTS, Python 3.10, NumPy 1.26.4, SciPy 1.15.3, UHD 4.9. Phần cứng đã thử là B205mini kết nối USB 3, antenna cổng RX2. Backend TurboFEC này yêu cầu CPU x86_64 có SSSE3. Chưa xác nhận chạy trên ARM hoặc AntSDR E200. Phiên bản UHD khác cần kiểm tra trên máy đích.

Trên Ubuntu, cài Python, bộ biên dịch và UHD có Python binding, rồi tạo môi trường cho phép dùng UHD của hệ thống:

```bash
sudo apt update
sudo apt install git python3-venv python3-pip python3-dev build-essential uhd-host python3-uhd
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python native/turbofec/build.py
python -c "import uhd; from pathlib import Path; print(uhd.__file__)"
python -m unittest discover -s tests -v
uhd_find_devices
```

Nếu UHD báo thiếu image FPGA/firmware, chạy tiện ích `uhd_images_downloader` tương ứng bản UHD đã cài. Không trộn Python binding UHD và thư viện UHD khác phiên bản. `requirements.txt` nêu khoảng phiên bản; `requirements-tested.txt` ghi phiên bản Python packages trên máy đã kiểm tra, không thay thế gói UHD hệ thống.

## Chạy terminal

Từ thư mục gốc repository, bật và kết nối drone/controller trước khi thu:

```bash
source .venv/bin/activate
mkdir -p sessions
python scripts/run_receiver.py -o sessions/test_01.fc32 --max-seconds 180 --max-candidates 100 --terminal overwrite
```

Lệnh dừng khi chạm giới hạn thời gian hoặc 100 candidate (đoạn tín hiệu được chọn), không phải 100 bản tin CRC đúng. Mỗi lượt phải dùng tên output mới. Ctrl+C dừng có lưu kết quả. `--terminal json` xuất bản tin JSON theo dòng; `--terminal overwrite` ghi đè bản tin trên terminal. Có thể thêm `--serial SERIAL` để chọn B205mini khi có nhiều thiết bị.

Wrapper mặc định bật center-first, predictor-shadow, rx-boundary=0.175 và predictor-control. CPU + async + hybrid, gain 30 dB, 20 MS/s, băng thông analog tối đa 20 MHz, dwell danh định 0.7 s/kênh và hit timeout 1.3 s được giữ theo entrypoint hiện hành. Tập kênh MHz: 2414.5, 2429.5, 2444.5, 2459.5, 5756.5, 5776.5, 5796.5, 5816.5. Không thu đồng thời cả tập kênh và không tự chuyển lên 50 MS/s.

Khi burst qua kiểm tra ZC/CP, hệ thống tạo lịch quay lại cùng kênh tại +0.640 và +1.280 giây, chưa cần CRC đúng. Main thread điều khiển phần cứng tại ranh giới khối thu ngắn; detector và decoder xử lý bất đồng bộ. Ngân sách chuyển tần phụ thuộc cặp kênh và được cập nhật từ phép đo trong phiên. Không dự đoán chuỗi nhảy giữa các kênh. Hết lịch hữu hạn thì quay lại quét/giữ kênh thông thường. Bộ giải mã giữ center-first, kiểm tra pilot/OFDM, hiệu chỉnh CFO/pha, cân bằng kênh, QPSK, Turbo và kiểm tra CRC; các fallback vẫn giữ nguyên.

Để chạy cấu hình đối chứng không điều khiển dự đoán:

```bash
python scripts/run_receiver.py --baseline -o sessions/baseline_01.fc32 --max-seconds 180 --max-candidates 100 --terminal json
```

Không mang số đo chuyển tần của máy cũ sang mặc định máy mới. Nếu có bộ hiệu chuẩn phù hợp, có thể thêm `--predictor-budget PATH` (JSON có trường `retune_pair_ms`). Mặc định scheduler dùng ngân sách dự phòng và cập nhật từ phép đo thực tế. Phải thu smoke và kiểm tra lỗi RX, queue drop, thời gian xử lý trên máy mới trước khi coi kết quả tương đương máy cũ.

## Chạy web

```bash
source .venv/bin/activate
python web/server.py --port 8088
```

Mở http://127.0.0.1:8088. Chỉ khi bấm **Bắt đầu thu** web mới khởi động receiver. Mặc định 180 giây, giới hạn 100000 candidate để không dừng sớm ở 100. Bấm Dừng để lưu; đóng server cũng gửi yêu cầu dừng tiến trình do server tạo. Web đếm bản tin CRC đúng và điểm có tọa độ hợp lệ riêng biệt; bản tin không có tọa độ vẫn được đếm. Marker và trace nhóm theo serial. Xuất JSON tải trạng thái cùng toàn bộ decoded rows của phiên đang xem. Chỉ xem phiên chạy từ web hiện tại; chưa có giao diện mở lại lịch sử. Nền bản đồ cần Internet, Leaflet JS/CSS đã lưu cục bộ. Web mặc định chỉ lắng nghe localhost; nếu cần truy cập LAN dùng `--host 0.0.0.0` trong mạng tin cậy, ứng dụng chưa có đăng nhập.

Không chạy terminal receiver và web receiver cùng lúc trên một SDR. Muốn dùng một bộ thu độc lập với web, chạy terminal và tự đọc `.decoded.jsonl`; UI hiện tại tự quản lý tiến trình riêng.

## Dữ liệu đầu ra và cách đánh giá

- `.fc32`: complex float32 interleaved, các cửa sổ candidate được cắt rồi ghép; **không phải raw liên tục** và không giữ thời gian thực giữa các gói.
- `.records.jsonl`: metadata của các cửa sổ IQ; dùng để liên hệ từng candidate với thời điểm/tần số ban đầu.
- `.events.jsonl`: nhật ký thu và xử lý.
- `.decoded.jsonl`: kết quả giải mã từng candidate, gồm trạng thái CRC và payload khi có.
- `.summary.json`: tổng kết phiên, lỗi RX, hàng đợi, bộ đếm và thống kê tốc độ.
- `.shadow.jsonl`: timing/dự đoán và các quan sát đi kèm khi bật predictor-shadow.

Phân biệt candidate, burst qua ZC/CP, bản tin parse được và bản tin CRC đúng. `decoded_messages/s` dùng số bản tin CRC đúng chia thời gian phiên, không phải recall (tỷ lệ thu được toàn bộ gói phát trong không gian). GPS hợp lệ là một tiêu chí riêng. Giữ metadata cùng IQ nếu chuyển dữ liệu kiểm chứng sang máy khác.

## Bằng chứng hiện có và giới hạn

Phiên Mini 3 điều khiển dự đoán gần nhất trên máy cũ: 100 candidate → 100 qua ZC → 89 CRC đúng trong 145.5606 s, tương đương 0.61143 bản tin CRC đúng/s; không ghi nhận lỗi RX hoặc queue drop. Đây là 89/100 candidate của phiên, không phải 89% tổng số gói drone phát. Lượt shadow trước đó: 100 candidate → 98 ZC → 85 CRC trong khoảng 146.068 s. Hai phiên thu khác thời điểm nên chưa chứng minh cải thiện nhân quả của predictor.

Bộ bàn giao được kiểm tra build TurboFEC và unit tests độc lập; xem VALIDATION.md. Không thực hiện thêm thu live trong đợt đóng gói. Kết quả RF trên máy mới vẫn phải kiểm chứng, đặc biệt băng thông USB, CPU, UHD, gain và vị trí antenna.

## Giấy phép

Giữ LICENSE của dự án gốc (AGPL-3.0), mã TurboFEC và LICENSE riêng (GPL-3.0-or-later), cùng thông tin nguồn gốc. Leaflet 1.9.4 dùng BSD-2-Clause; xem web/static/vendor/LICENSE. Khi phân phối cần giữ các thông tin giấy phép tương ứng.
