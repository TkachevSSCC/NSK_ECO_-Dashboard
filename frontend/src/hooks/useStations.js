/*
 * Модуль для загрузки станций с сервера каждую секунду useStations.js
 * (интервал совпадает с тиком метрик на бэкенде — TICK_SECONDS = 1)
 */

//Импорт модулей

import { useEffect, useState } from "react";
import { getStations } from "../services/api";

//Функция для загрузки станций

export const useStations = () => {
  const [stations, setStations] = useState([]);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const data = await getStations();
        setStations(data);
      } catch (error) {
        console.error("Ошибка загрузки станций:", error);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 1000);

    return () => clearInterval(interval);
  }, []);

  return stations;
};
