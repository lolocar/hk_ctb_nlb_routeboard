# 项目背景

## 目标
制作一个巴士到站时间 webapp，覆盖两家公司：**城巴（CTB）** 和 **新世界第一巴士（NLB）**。

## 核心功能
- 利用 **LBS 信息**（用户 GPS 定位）找到用户附近/关注的巴士站
- 利用 **用户订阅信息**（用户收藏/订阅的巴士站或路线）
- 展示巴士站各路线的**预期到站时间**（ETA）

## API 架构（已知背景）
- 两家公司的 API 架构**套路类似**，但**所用字段不同**（需要各自适配解析层）
- 存在**通用接口**：可查询**个别车站**的**各路线到站时间**（以 stop 为入口，返回该站所有路线的 ETA）
- 设计含义：前端按统一数据模型（station → routes[] → ETAs）展示，后端/适配层负责把两家公司的不同字段映射到这个统一模型

## 路线列表 API（已实现导入）
### 城巴 CTB
- URL: `https://rt.data.gov.hk/v1/transport/citybus-nwfb/route/ctb`
- 响应: `{"type":"RouteList","version":"1.0","generated_timestamp":"...","data":[...]}`
- 每条记录字段: `co`(="CTB"), `route`(路线号), `orig_tc/orig_sc/orig_en`, `dest_tc/dest_sc/dest_en`, `data_timestamp`
- 特点: **无独立 routeId**，route 号即标识；每条记录 = 一个方向（orig→dest 分开字段）
- 导入实测: 406 条

### 新巴/新大屿山 NLB
- URL: `https://rt.data.gov.hk/v2/transport/nlb/route.php?action=list`
- 响应: `{"routes":[...]}`
- 每条记录字段: `routeId`(数字ID), `routeNo`(路线号), `routeName_c/routeName_s/routeName_e`(用 " > " 拼接起讫点), `overnightRoute`(0/1), `specialRoute`(0/1)
- 特点: **有独立 routeId**；同一 routeNo 可有多条 routeId（往返/支线变体）；routeName 需按 " > " 拆分起讫点；无时间戳
- 导入实测: 64 条

## 统一数据模型（SQLite `route` 表）
- `operator`(CTB/NLB), `source_route_id`(源ID，CTB用route号), `route_no`, `direction`(同routeNo内的方向序号)
- `origin_tc/sc/en`, `destination_tc/sc/en`, `overnight`, `special`, `data_timestamp`, `fetched_at`
- 唯一键: (operator, source_route_id, direction)

## 站点 API（已实现导入）
### 城巴 CTB（两步走）
1. 路线站点序列（**无位置**）:
   `https://rt.data.gov.hk/v1/transport/citybus-nwfb/route-stop/ctb/{route}/{inbound|outbound}`
   响应: `{"type":"RouteStop","data":[{co,route,dir("I"/"O"),seq,stop,data_timestamp},...]}`
   - 每条路线需分别请求 inbound / outbound 两个方向
   - dir: "I"=inbound, "O"=outbound
2. 站点详情（名称+坐标）:
   `https://rt.data.gov.hk/v1/transport/citybus-nwfb/stop/{stop_id}`
   响应: `{"type":"Stop","data":{stop,name_tc,name_sc,name_en,lat,long,data_timestamp}}`
   - **注意字段名是 `lat` / `long`**（不是 lng/lon）
   - 无 location（路段）字段
- 优化: 先收集所有路线 x 2 方向的 stop id 与 (route,dir,seq) 关联，去重后逐个 stop id 只请求一次详情
- 请求量大: 406 路线 x 2 方向 + ~数百去重站点详情 ≈ 1000+ 次请求，耗时数分钟

### 新巴/新大屿山 NLB（一步到位）
- `https://rt.data.gov.hk/v2/transport/nlb/stop.php?action=list&routeId={routeId}`
- 响应: `{"stops":[{stopId, stopName_c/s/e, stopLocation_c/s/e, latitude, longitude, fare, fareHoliday, someDepartureObserveOnly},...]}`
- 站点**直接带** latitude/longitude，无需二次请求
- **每个方向使用独立 routeId**：同一 routeNo 的多个 routeId（route 表每行）各对应一个方向/变体，需逐一请求
  - 例: B2X 去程 routeId=35（4 站），返程 routeId=36（3 站），站点列表不同
- 有 stopLocation（路段）字段，CTB 没有

## 站点数据模型（SQLite `stop` + `route_stop` 表）
### `stop`（物理站点，跨路线共享）
- `operator`, `source_stop_id`(CTB:"002511" / NLB:"176"), `name_tc/sc/en`, `location_tc/sc/en`(仅NLB), `latitude`, `longitude`, `data_timestamp`, `fetched_at`
- 唯一键: (operator, source_stop_id)
### `route_stop`（路线-方向-站点关联，带顺序）
- `operator`, `source_route_id`, `direction`, `seq`(从1开始), `source_stop_id`, `fetched_at`
- 唯一键: (operator, source_route_id, direction, seq)
- 关联 route 表: (operator, source_route_id, direction)

## 项目结构
```
routeboard.db          # SQLite 数据库
db.py                  # schema + 连接 + upsert 函数（route/stop/route_stop）
import_routes.py       # 路线导入 CLI（python import_routes.py [ctb|nlb]）
import_stops.py        # 站点导入 CLI（python import_stops.py [ctb|nlb]）
importers/
  ctb.py               # 城巴路线: fetch/parse/import_ctb_routes
  nlb.py               # 新巴路线: fetch/parse/import_nlb_routes
  ctb_stops.py         # 城巴站点: fetch_ctb_route_stops/fetch_ctb_stop_detail/import_ctb_stops
  nlb_stops.py         # 新巴站点: fetch_nlb_route_stops/import_nlb_stops
```
导入策略: 全量替换（先 DELETE 该公司旧数据再 INSERT）
站点导入依赖 route 表（需先运行 import_routes.py）

## 待确认 / 待调研
## 站点导入（已实现并跑通）
- CTB: 2584 站, 17340 路线-站点关联; NLB: 290 站, 1418 关联
- CTB 导入流程（重构后）:
  1. Phase 1: 遍历所有路线 x 2 方向，提取 stop ID（`stop` 字段），建立 (route,dir,seq,stop_id) 关联
  2. Phase 2: 对去重后的 stop ID 逐个请求 `stop/{id}` 查详情（名称+坐标）——**CTB 无批量接口**
  3. Phase 3: 统一写库
- 单向路线: 一个方向 inbound/outbound 返回空是正常现象（约 103 条 CTB 路线）
- 特殊路线: 两个方向都无站点数据（约 9 条 CTB 路线，如 9C/H0/NT3 等），不用纠结
- 所有站点 latitude/longitude 均非空（0 条 NULL）

## 实时到站 ETA 接口（已验证，实时数据不入库）
- URL: `https://rt.data.gov.hk/v1/transport/batch/stop-eta/{co}/{stop_id}?lang={lang}`
  - `co`: `ctb`（城巴）/ `nlb`（新大屿山巴士）
  - `stop_id`: 站点 ID（CTB 形如 "001001"，NLB 形如 "176"）
  - `lang`: `zh-hant`（繁中）/ `zh-hans`（简中）/ `en`（英文）
- 响应: `{"type":"StopETA","version":"1.0","generated_timestamp":"...","data":[...]}`
- 每条记录字段: `co`, `route`(路线号), `dir`(CTB:"I"/"O"; NLB:null), `seq`(CTB有/NLB null),
  `stop`, `dest`(目的地，按 lang 翻译), `eta_seq`(第几班车; NLB null), `eta`(ISO 时间，无班次时为空串),
  `rmk`(备注，如"此路線於未來60分鐘沒有班次途經本站"; 按 lang 翻译), `routeVariantName`,
  `departed`, `noGPS`, `wheelChair`, `data_timestamp`
- **关键**: 同一 (route, dir) 可有多条记录（eta_seq=1,2,3... = 未来第1/2/3班车）；
  无班次时 `eta` 为空串、`rmk` 有提示文字。前端取 eta_seq=1（或 eta 非空的第一条）作为"下一班"。
- 实测: CTB 001001 返回多路线多班次；NLB 176 返回 2 方向（B2X 去/返）

## Webapp（已实现，纯原生 JS 单文件，零依赖）
### 文件
- `webapp/index.html`：单文件应用（HTML+CSS+JS）
- `webapp/stops.json`：从 DB 导出的站点数据（2874 站：CTB 2584 + NLB 290，含三语名+坐标）
- 导出脚本：`python3 -c "..."` 从 `routeboard.db` 的 stop 表导出（见下方"重新导出"）

### 功能
- **站点列表**：从 stops.json 读，全量 2874 站
- **LBS 500m 附近**：📍 按钮取 GPS，Haversine 算距离，500m 内按距离排序
  - 定位成功后主列表**默认只显示"订阅 + 附近500m"**（不追加全部），底部"查看全部巴士站"按钮可展开/收起
  - 未定位成功时显示全部（无 500m 概念）
- **订阅**：★ 按钮（列表页 + 详情页标题旁都有），**Cookie 保存**（`subscribed` cookie，JSON 数组 `["CTB:001001",...]`，365 天）
  - 订阅站点置顶"⭐ 已訂閱"区块；附近+订阅可重复展示
- **搜索**：繁中/简中/英文名 + 站点 ID 模糊匹配
- **站点详情**：调 `stop-eta` 接口，按 `route|dest` 分组（CTB 同方向 dest 同；NLB 不同方向 dest 不同、dir 皆 null），
  取每组 eta_seq=1（或首条 eta 非空）为下一班；ETA 徽章配色（≤1min 红"即將到站"、<10 红、<30 橙、遠 绿、無 灰）；
  显示 rmk 备注；**30s 轮询**刷新
- **语言**：繁中/英文（Cookie `lang`），影响界面文字、站点名、ETA 接口 lang 参数
- 响应式（max-width 640px 居中），移动端友好

### 部署
- 本地：`cd webapp && python3 -m http.server 8090` → http://127.0.0.1:8090
- 生产：丢到任意静态托管（Vercel/Netlify/GitHub Pages）自动 HTTPS；浏览器直连 rt.data.gov.hk（CORS `Access-Control-Allow-Origin: *` 已验证）
- ETA 实时数据不入库，浏览器每次直调 API

### 重新导出 stops.json
```python
import sqlite3, json
conn = sqlite3.connect('routeboard.db'); conn.row_factory = sqlite3.Row
stops = [{"op":r["operator"],"id":r["source_stop_id"],"tc":r["name_tc"] or "","sc":r["name_sc"] or "","en":r["name_en"] or "",
          "loc_tc":r["location_tc"] or "","loc_sc":r["location_sc"] or "","loc_en":r["location_en"] or "",
          "lat":r["latitude"],"lng":r["longitude"]}
         for r in conn.execute("SELECT * FROM stop WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY operator, source_stop_id")]
json.dump({"generated":"...","count":len(stops),"stops":stops}, open('webapp/stops.json','w'), ensure_ascii=False, separators=(',',':'))
```

### 已知限制
- 订阅存 Cookie：换浏览器/清 Cookie 会丢，无账号同步
- 无定位权限时附近区块隐藏，仍可用搜索
- 站点数据是静态导出，路线/站点变动需重跑 import_stops.py + 重新导出（或等每日维护）

### 验证方式
- 无 headless 浏览器环境，用 Node `vm` + DOM 桩执行 index.html 内嵌 JS 做行为断言
- 已验证：定位后默认只看附近500m（含展开/收起切换）、详情页订阅按钮完整流程（打开→ETA→订阅→Cookie 写入→取消→返回）
- 期间发现并修复真实 bug：`computeNearby` 原用 `.map(s=>({...s,dist}))` 生成新对象，
  导致 `renderList` 里 `filtered.includes(s)`（按引用比较）永远失配、附近区块恒为空；
  改为保留原对象引用、只附加 dist 字段

## 每日自动维护（webapp 服务自己定时，不用 crontab）
### 机制
- `serve_https.py` 启动时起后台守护线程 `_maintenance_loop`：计算下一个 HKT 05:00，sleep 到点，
  调 `maintenance.run_maintenance()`，循环（每天一次）
- 服务器重启后线程重新算下一个 05:00，行为正确
### 维护流程（`maintenance.py`，只增不减、幂等）
1. `detect_new_routes(conn)`：拉 CTB/NLB 路线列表 API，对比 DB route 表，找出新增路线
   - CTB 按 route_no 判重；NLB 按 source_route_id(routeId) 判重
2. 增量 upsert 新路线进 route 表
3. 抓新路线站点：
   - CTB：`fetch_ctb_route_stops`（inbound/outbound）收集 stop id + (route,dir,seq) 关联，
     去重后只对 DB 没有的 stop id 调 `fetch_ctb_stop_detail` 查坐标（省请求）
   - NLB：`fetch_nlb_route_stops` 每个 routeId 一次，站点自带坐标
4. `export_stops_json()` 重新导出 `webapp/stops.json` + `stops_meta.json`（原子替换：临时文件 + os.replace）
- 已停办路线的站点保留（站点物理存在；详情页由 stop-eta 实时接口决定显示哪些路线）
### 文件
- `export_stops.py`：`export_stops_json(db_path, out_dir)` → stops.json + stops_meta.json（count+updated）
- `maintenance.py`：`detect_new_routes` / `import_new_ctb_stops` / `import_new_nlb_stops` / `run_maintenance(db_path, out_dir, check_only)`
- `webapp/serve_https.py`：加 `_maintenance_loop` 线程 + `--check-now`（启动时立即查一次）/ `--no-maint`
- 手动：`python3 maintenance.py [--check-only]`
### 前端感知更新
- 前端每 10 分钟 fetch `stops_meta.json`（轻量，几十字节），比对 `updated` 时间戳
- 变化则重新 fetch `stops.json`，刷新列表 + 显示"数据已更新"banner（8 秒自动消失）
### 验证
- 模拟新路线：临时 DB 删 CTB 路线 1 → run_maintenance 检测为"新"，恢复 route + 38 关联，stops.json 重新导出 ✓
- 服务启动：维护线程打印"next check at ... 05:00"，--check-now 立即查"no new routes" ✓
- HTTPS 端点：index.html / stops.json / stops_meta.json 均 200 ✓

## 待确认 / 待调研
- [x] 通用车站到站接口的具体 endpoint 和字段定义（CTB / NLB 各一）→ 见上
- [ ] 前端展示形式：地图选站？列表？订阅管理？
- [ ] 技术栈选择
- [ ] 数据刷新频率 / 轮询间隔
- [ ] 用户订阅的存储方式（localStorage / 后端）

## 备注
- 工作目录：`D:\git2\hk_ctb_nlb_routeboard`
- 项目名中的 ctb / nlb 即城巴 / 新巴
