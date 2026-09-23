import numpy as np
import sklearn
from sklearn.cluster import KMeans
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import matplotlib.lines as mlines
from pyproj import Transformer
import contextily as ctx
from datetime import datetime
from fastapi.responses import FileResponse
import matplotlib.image as mpimg

def time_zone_schedule(stations, capacity, ax):
    rng = np.random.default_rng(42)
    batteries = rng.uniform(low=0, high=1, size=100)
    coords = [[s['latitude'], s['longitude']] for s in stations]
    coords = np.array(coords)
    x_min = coords[:, 0].min() - 0.01
    x_max = coords[:, 0].max() + 0.01
    y_min = coords[:, 1].min() - 0.01
    y_max = coords[:, 1].max() + 0.01
    num_zones = int(coords.shape[0] // capacity) + 5
    lines = np.linspace(x_min, x_max, num_zones + 1)
    point_number = []
    for idx, line in enumerate(lines[1:]):
        point_number.append(coords[(coords[:, 0] <= lines[idx+1]) & (coords[:, 0] >= lines[idx])].shape[0])
    for idx, num in enumerate(point_number):
        if num > capacity:
            lines[idx] += 0.01
    time_zones = []
    for idx, line in enumerate(lines[1:]):
        #time_zones.append(coords[(coords[:, 0] <= lines[idx+1]) & (coords[:, 0] >= lines[idx])])
        time_zones.append(np.where((coords[:, 0] <= lines[idx+1]) & (coords[:, 0] >= lines[idx]))[0])
    ax.plot(coords[:, 0], coords[:, 1], "k.", markersize=6)
    for line in lines:
        ax.axline((line, y_min), (line, y_max))
    ax.set_title("Time zone schedule")
    return time_zones


def clustering_schedule(stations, capacity, ax, mode=None):
    rng = np.random.default_rng(42)
    batteries = rng.uniform(low=0, high=1, size=100)
    coords = [[s['latitude'], s['longitude']] for s in stations]
    coords = np.array(coords)
    #stations_new = coords, batteries
    #coords, batteries = stations
    x_min = coords[:, 0].min() - 0.01
    x_max = coords[:, 0].max() + 0.01
    y_min = coords[:, 1].min() - 0.01
    y_max = coords[:, 1].max() + 0.01
    num_clusters = int(coords.shape[0] // capacity) + 5
    df = pd.DataFrame(coords, columns=['lat', 'lon'])
    kmeans = KMeans(n_clusters=num_clusters, random_state=0).fit(df)
    h = 0.0005  # point in the mesh [x_min, x_max]x[y_min, y_max].
    xx, yy = np.meshgrid(np.arange(x_min, x_max, h), np.arange(y_min, y_max, h))
    Z = kmeans.predict(np.c_[xx.ravel(), yy.ravel()])
    Z = Z.reshape(xx.shape)
    ax.imshow(
        Z,
        interpolation="nearest",
        extent=(xx.min(), xx.max(), yy.min(), yy.max()),
        cmap=plt.cm.tab20,
        aspect="auto",
        origin="lower",
    )
    ax.plot(coords[:, 0], coords[:, 1], "k.", markersize=6)
    clustering_zones = []
    cluster_heads = np.zeros((num_clusters, 2, 2)) 
    for idx in range(num_clusters):
        clustering_zones.append(np.where(kmeans.labels_ == idx)[0])
    if mode is not None:
        if mode == "battery_life":
            for idx, cluster_center in enumerate(kmeans.cluster_centers_):
                cluster_points = coords[kmeans.labels_ == idx]
                cluster_batteries = batteries[kmeans.labels_ == idx]
                closest_stations = np.argsort(cluster_batteries)[::-1]
                cluster_heads[idx, 0] = cluster_points[closest_stations[0]]
                cluster_heads[idx, 1] = cluster_points[closest_stations[1]]
            ax.set_title("Clustering with battery life based cluster heads")
        else:
            if mode != "proximity":
                print("Not a valid clustering mode! Defaulting to proximity")
            for idx, cluster_center in enumerate(kmeans.cluster_centers_):
                cluster_points = coords[kmeans.labels_ == idx]
                dists = np.linalg.norm(cluster_points - cluster_center, axis=1)
                closest_stations = np.argsort(dists)
                cluster_heads[idx, 0] = cluster_points[closest_stations[0]]
                cluster_heads[idx, 1] = cluster_points[closest_stations[1]]
            ax.set_title("Clustering with proximity based cluster heads")
        ax.scatter(
            cluster_heads[:, 0, 0],
            cluster_heads[:, 0, 1],
            marker="x",
            s=160,
            linewidths=3,
            color="r",
            zorder=10,
        )
        ax.scatter(
            cluster_heads[:, 1, 0],
            cluster_heads[:, 1, 1],
            marker="x",
            s=100,
            linewidths=3,
            color="w",
            zorder=10,
        )
        
    else:
        ax.set_title("Clustering without cluster heads")
            
    return kmeans, clustering_zones, cluster_heads

def clustering_schedule_json(stations, capacity, ax, mode=None):
    rng = np.random.default_rng(42)
    batteries = rng.uniform(low=0, high=1, size=100)
    coords = [[s['latitude'], s['longitude']] for s in stations]
    coords = np.array(coords)
    print(coords[:10])
    print(coords.min(axis=0))
    print(coords.max(axis=0))
    x_min = coords[:, 0].min() - 0.01
    x_max = coords[:, 0].max() + 0.01
    y_min = coords[:, 1].min() - 0.01
    y_max = coords[:, 1].max() + 0.01
    num_clusters = int(coords.shape[0] // capacity) + 5
    df = pd.DataFrame(coords, columns=['lat', 'lon'])
    kmeans = KMeans(n_clusters=num_clusters, random_state=0).fit(df)
    h = 0.0005  # point in the mesh [x_min, x_max]x[y_min, y_max].
    xx, yy = np.meshgrid(np.arange(x_min, x_max, h), np.arange(y_min, y_max, h))
    Z = kmeans.predict(np.c_[xx.ravel(), yy.ravel()])
    Z = Z.reshape(xx.shape)
    img = mpimg.imread('/Users/matteweee/projects/nsk_eco/backend/app/novosibirsk_map.png')
    ax.imshow(img)

    plt.show()
    # ax.imshow(
    #     img,
    #     # extent=(54.9200, 55.1404, 82.7535, 83.1548),  # ВАЖНО: подгонка координат
    #     aspect='auto',
    #     alpha=0.9,
    #     zorder=0
    # )
    # ax.imshow(
    #     Z,
    #     interpolation="nearest",
    #     extent=(xx.min(), xx.max(), yy.min(), yy.max()),
    #     cmap=plt.cm.tab20,
    #     aspect="auto",
    #     origin="lower",
    # )
    # ax.set_xlim(82.7535, 83.1548)
    # ax.set_ylim(54.9200, 55.1404)
    # ax.plot(coords[:, 0], coords[:, 1], "k.", markersize=6)
    clustering_zones = []
    cluster_heads = np.zeros((num_clusters, 2, 2)) 
    for idx in range(num_clusters):
        clustering_zones.append(np.where(kmeans.labels_ == idx)[0])
    if mode is not None:
        if mode != "proximity":
            print("Not a valid clustering mode! Defaulting to proximity")
        for idx, cluster_center in enumerate(kmeans.cluster_centers_):
            cluster_points = coords[kmeans.labels_ == idx]
            dists = np.linalg.norm(cluster_points - cluster_center, axis=1)
            closest_stations = np.argsort(dists)
            cluster_heads[idx, 0] = cluster_points[closest_stations[0]]
            #cluster_heads[idx, 1] = cluster_points[closest_stations[1]]
        ax.set_title("Clustering with proximity based cluster heads")
        # ax.scatter(
        #     cluster_heads[:, 0, 0],
        #     cluster_heads[:, 0, 1],
        #     marker="x",
        #     s=160,
        #     linewidths=3,
        #     color="r",
        #     zorder=10,
        # )
        # ax.scatter(
        #     cluster_heads[:, 1, 0],
        #     cluster_heads[:, 1, 1],
        #     marker="x",
        #     s=100,
        #     linewidths=3,
        #     color="w",
        #     zorder=10,
        # )
        
    else:
        ax.set_title("Clustering without cluster heads")
            
    return kmeans, clustering_zones, cluster_heads

