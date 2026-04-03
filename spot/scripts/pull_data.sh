cd "$(dirname "$0")/../.."

python -m spot.main --prepare-backtest-data \
  --backtest-data-source realtime \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --backtest-start 2022-03-03 \
  --backtest-end 2026-03-03 \
  --history-max-rows-per-symbol 0 \
  --api-max-requests-per-minute 360 \
  --api-rate-limit-retries 8 \
  --api-rate-limit-backoff-sec 0.8 \
  --api-rate-limit-backoff-max-sec 12 \
  --history-fetch-concurrency 1 \
  --history-page-sleep-sec 0.15 \
  --backtest-data-file ./spot/history/bt_20220303_20260303.json.gz
