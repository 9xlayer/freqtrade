# AGENT OPERATING PRINCIPLES & VERIFICATION STANDARDS

## 1. Nguyên tắc Cốt lõi (Core Architecture Principles)

- **Ưu tiên tính tất định (Determinism Over Prose):** Không dùng văn bản, lời hứa hay comment để bù đắp cho kiến trúc yếu. Mọi quy tắc kỹ thuật phải được cưỡng chế thông qua hệ thống kiểu (type system), cấu trúc thư mục, thiết kế API an toàn và quy tắc Lint.
- **Bảo vệ độ sạch của Codebase (Codebase Hygiene):** Codebase hiện tại là dữ liệu đầu vào (input) lớn nhất quyết định chất lượng code sinh ra tiếp theo. Tuyệt đối không để lại workaround, debt hoặc anti-pattern vì các phiên Agent sau sẽ coi đó là mẫu chuẩn để nhân bản.
- **Sửa tận gốc (Root-Cause Resolution):** Khi gặp lỗi biên dịch, lỗi Lint hoặc xung đột luồng, phải giải quyết triệt để nguyên nhân gốc rễ. Nghiêm cấm các hành vi né tránh kiểm tra (tắt rule, ép kiểu bừa bãi, che giấu lỗi).

---

## 2. Quy chuẩn Viết Code (Coding Standards)

- **Hạn chế tối đa Comment (99% No-Comment Policy):**
  - Không viết comment để giải thích hoặc biện minh cho một giải pháp chắp vá.
  - Code phải tự giải thích thông qua đặt tên rõ nghĩa và cấu trúc mạch lạc. Chỉ giữ comment cho các logic nghiệp vụ đặc thù không thể diễn đạt qua code.
- **Tôn trọng ranh giới kiến trúc:**
  - Tuân thủ nghiêm ngặt phân tách trách nhiệm giữa các module/thư mục. Không import chéo vi phạm phân tầng kiến trúc.
  - Tránh lạm dụng các API hoặc pattern có tính mơ hồ cao, dễ phát sinh side-effect ngầm; ưu tiên luồng dữ liệu tường minh, dễ dự đoán.
- **Vòng lặp cải thiện rào cản (Guardrail Loop):**
  - Mỗi khi phát hiện một lỗi lặp lại hoặc một anti-pattern mới trong dự án, phải đề xuất chuyển hóa bài học đó thành **quy tắc kiểm tra tự động** (Lint rule kèm thông báo lỗi hướng dẫn cách sửa đúng) và dọn sạch các đoạn code cũ vi phạm.

---

## 3. Quy trình Kiểm chứng & Bàn giao (Verification & Hand-off)

Không bàn giao code chỉ dựa trên phán đoán tĩnh. Mọi thay đổi phải đi kèm bằng chứng kiểm chứng ở cấp độ tương ứng:

### A. Kiểm chứng thực thi (Runtime Verification)

- Tự chạy ứng dụng/môi trường thông qua các công cụ CLI hoặc test runner của dự án để xác nhận thay đổi hoạt động thực tế.
- Luôn đối chiếu hành vi dưới góc nhìn và thuật ngữ của người dùng cuối (User-centric Feature Map), tránh suy diễn theo cấu trúc nội bộ.

### B. Mô hình hóa & Kiểm chứng luồng phức tạp (State Modeling & Invariants)

Đối với các luồng nghiệp vụ nhiều trạng thái, bất đồng bộ hoặc có tính chất phân tán:

1. **Định nghĩa bất biến (Invariants):** Xác định rõ các điều kiện hệ thống tuyệt đối không được vi phạm trước khi viết logic.
2. **Trực quan hóa cho người review (Human-Readable Statechart):**
   - Xuất sơ đồ trạng thái (Mermaid `stateDiagram-v2`) tóm tắt luồng hoạt động để con người duyệt nhanh.
   - **Tiêu chuẩn sơ đồ:** Sử dụng ngôn ngữ tự nhiên dưới góc nhìn người dùng (tuyệt đối không dùng tên biến/hàm kỹ thuật); giữ biểu đồ tinh gọn (**tối đa ~20 mũi tên**); tô nổi bật các trạng thái hoặc nhánh có nguy cơ phá vỡ bất biến.
3. **Tái hiện thực tế (Replay to Real Code):** Mọi kịch bản lỗi hoặc trường hợp biên phát hiện qua mô hình trạng thái đều phải được chuyển thành bài test tự động chạy trên code thật để chứng minh đã xử lý dứt điểm.
