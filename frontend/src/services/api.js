import axios from "axios";

const api = axios.create({
  baseURL: "",
});

export const getStations = async () => {
  const response = await api.get("/stations");
  return response.data;
};
