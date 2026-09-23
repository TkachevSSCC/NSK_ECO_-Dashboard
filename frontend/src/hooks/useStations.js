/*
 * Модуль для загрузки станций с сервера каждые 2 секунды useStations.js
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
    const interval = setInterval(fetchData, 2000);

    return () => clearInterval(interval);
  }, []);

  return stations;
};
