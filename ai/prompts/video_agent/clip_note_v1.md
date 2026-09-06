[CLIP]
이 영상은 원본 영상(총 {{original_duration}}초)에서 {{clip_start}}~{{clip_end}} 구간만 잘라낸 {{clip_length}}초짜리 클립이다.
- 모든 시각(timestamp, time, first_seen, last_seen, start/end, timeline)은 이 클립의 시작을 00:00.0 으로 하여 적는다. 원본 시각으로 바꾸지 않는다 (코드가 되돌린다).
- <PREVIOUS_RESULT> 와 <FOCUS> 의 시각도 같은 클립 기준으로 이미 변환되어 있다.
- 클립 밖(앞·뒤)에 있었던 차량·사건은 보이지 않는 것이 정상이다. 보이지 않는다는 이유로 이전 결과의 차량을 지우거나 "없다"고 단정하지 말고, vehicles 목록과 ID 는 그대로 유지한다.
- 클립 시작 전에 이미 일어난 일(선진입, 차로 변경 시작 등)은 UNKNOWN 으로 두고 unknown_or_unobservable 에 "클립 이전 구간" 이라고 적는다.
