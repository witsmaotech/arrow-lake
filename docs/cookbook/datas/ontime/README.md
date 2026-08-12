# ontime 示例数据（2022 年美国航班数据）

`ontime_2022.parquet` 是美国交通部交通统计局（BTS）On-Time Performance 数据集的 2022 年子集，用于 Arrow Lake Cookbook 的 OLAP 分析与 SQL 查询演示（主要见 [07 OLAP 分析](../07-olap-analytics-zh.md)）。

## 数据规模

| 项 | 值 |
|---|---|
| 时间范围 | 2022 年全年 |
| 行数 | 1,598,468 |
| 列数 | 109 |
| 文件大小 | 57.7 MB |
| 格式 | Parquet |
| 来源 | US DOT BTS（公共领域，可自由使用） |

## 字段分组（109 列）

| 分组 | 主要字段 |
|---|---|
| 日期 / 时间 | `Year`, `Quarter`, `Month`, `DayofMonth`, `DayOfWeek`, `FlightDate` |
| 航班 / 航司 | `Reporting_Airline`, `IATA_CODE_Reporting_Airline`, `Tail_Number`, `Flight_Number_Reporting_Airline` |
| 出发机场 | `Origin`, `OriginCityName`, `OriginState`, `OriginAirportID`, `OriginStateName` |
| 到达机场 | `Dest`, `DestCityName`, `DestState`, `DestAirportID`, `DestStateName` |
| 出发时间 / 延误 | `CRSDepTime`, `DepTime`, `DepDelay`, `DepDelayMinutes`, `DepDel15`, `DepTimeBlk` |
| 滑行 / 轮档 | `TaxiOut`, `WheelsOff`, `WheelsOn`, `TaxiIn` |
| 到达时间 / 延误 | `CRSArrTime`, `ArrTime`, `ArrDelay`, `ArrDelayMinutes`, `ArrDel15`, `ArrTimeBlk` |
| 取消 / 备降 | `Cancelled`, `CancellationCode`, `Diverted` |
| 飞行时长 | `CRSElapsedTime`, `ActualElapsedTime`, `AirTime`, `Flights` |
| 距离 | `Distance`, `DistanceGroup` |
| 延误成因 | `CarrierDelay`, `WeatherDelay`, `NASDelay`, `SecurityDelay`, `LateAircraftDelay` |
| 备降详情 | `Div1`~`Div5` 系列（`DivAirportLandings`, `Div1Airport`, `Div1WheelsOn` 等） |

> **常用列**：日常分析最常用 `Reporting_Airline`、`Origin`、`Dest`、`Month`、`DepDelay`、`ArrDelay`、`Cancelled`、`Distance`，以及五个延误成因列（`CarrierDelay` 等）。

## 摄入

```bash
arrow-lake --base-uri ./ontime_lake ingest files ontime docs/cookbook/datas/ontime/ontime_2022.parquet
```

## 典型查询示例

```sql
-- 1. 各航空公司平均到达延误（GROUP BY 聚合）
SELECT Reporting_Airline,
       AVG(ArrDelay) AS avg_arr_delay,
       COUNT(*) AS flights
FROM ontime
GROUP BY Reporting_Airline
ORDER BY avg_arr_delay DESC;

-- 2. 各机场起飞延误最严重的航班（窗口函数）
SELECT Origin, Flight_Number_Reporting_Airline, DepDelay
FROM (
  SELECT *,
         RANK() OVER (PARTITION BY Origin ORDER BY DepDelay DESC) AS rk
  FROM ontime
  WHERE Cancelled = 0
)
WHERE rk <= 3;

-- 3. 月度航班量与延误趋势（时间序列）
SELECT Month,
       COUNT(*) AS flights,
       AVG(ArrDelay) AS avg_delay
FROM ontime
GROUP BY Month
ORDER BY Month;

-- 4. 延误成因占比
SELECT
  SUM(CarrierDelay)      / NULLIF(SUM(ArrDelay), 0) AS carrier_ratio,
  SUM(WeatherDelay)      / NULLIF(SUM(ArrDelay), 0) AS weather_ratio,
  SUM(NASDelay)          / NULLIF(SUM(ArrDelay), 0) AS nas_ratio,
  SUM(LateAircraftDelay) / NULLIF(SUM(ArrDelay), 0) AS late_aircraft_ratio
FROM ontime
WHERE ArrDelay > 0;

-- 5. 航线（出发-到达）距离与平均延误（多字段分组）
SELECT Origin, Dest,
       AVG(Distance)  AS avg_distance,
       AVG(ArrDelay)  AS avg_delay,
       COUNT(*)       AS flights
FROM ontime
WHERE Cancelled = 0
GROUP BY Origin, Dest
ORDER BY flights DESC
LIMIT 20;
```

## 数据来源

原始数据来自 [US DOT Bureau of Transportation Statistics](https://www.bts.gov/) 的 Airline On-Time Performance 数据，属美国公共领域数据，可自由使用与分发。原始全集（1987—至今）体积庞大，本子集仅取 2022 年以便在仓库中分发与快速演示。
