import pyvista as pv
import numpy as np
import tempfile
import open3d as o3d
import logging
from scipy.spatial import KDTree
from scipy.spatial import Delaunay
import random
from pointCloudToolbox import *
import copy
import itertools

def create_mesh_with_curvature(file_path):
    logging.info("Inside create_mesh_with_curvature()")

    # Parse the PLY file
    points = parse_ply(file_path)
    if points is None:
        raise ValueError("Failed to parse the PLY file.")

    # Create Open3D PointCloud object
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)

    # Estimate normals
    logging.info("Estimating normals for the point cloud...")
    # Get the bounding box of the point cloud
    bbox = pcd.get_axis_aligned_bounding_box()
    min_bound = np.array(bbox.min_bound)
    max_bound = np.array(bbox.max_bound)

    # Compute the diagonal length of the bounding box
    scale = np.linalg.norm(max_bound - min_bound)

    # Compute the radius as a fraction of the scale
    scale_fraction=0.1
    radius = scale * scale_fraction
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=50))
    pcd.orient_normals_consistent_tangent_plane(k=50)

    # Visualize the input point cloud with normals
    # o3d.visualization.draw_geometries([pcd], window_name="Input Point Cloud with Normals", mesh_show_back_face=True)

    # Calculate average distance and derive radii for BPA
    logging.info("Calculating radii using average distance...")
    metrics = average_distance_using_kd_tree(pcd)
    average_distance = metrics['average_distance']
    radii = metrics['radii_list']
    logging.info(f"Average distance: {average_distance}, Radii for BPA: {radii}")

    # Perform surface reconstruction using Ball Pivoting Algorithm
    logging.info("Performing Ball Pivoting Algorithm (BPA) for surface reconstruction...")
    bpa_mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
        pcd, o3d.utility.DoubleVector(radii)
    )

    # Check if BPA generated any triangles
    if not bpa_mesh.has_triangles():
        raise ValueError("Ball Pivoting Algorithm failed to generate any triangles. Check the input point cloud and radii.")

    # clean the mesh
    logging.info("Cleaning the mesh...")
    bpa_mesh.remove_degenerate_triangles()
    bpa_mesh.remove_duplicated_triangles()
    bpa_mesh.remove_unreferenced_vertices()

    # Convert to PyVista mesh
    logging.info("Converting Open3D mesh to PyVista format...")
    vertices = np.asarray(bpa_mesh.vertices)
    triangles = np.asarray(bpa_mesh.triangles)

    if len(triangles) == 0:
        raise ValueError("No triangles were generated in the mesh.")

    # PyVista expects flattened faces
    faces = np.hstack([[3] + list(tri) for tri in triangles])
    pv_mesh = pv.PolyData(vertices, faces)

    # Fill small holes in the mesh
    mesh = convert_pv_to_o3d(pv_mesh)

    # Repair the mesh
    if not mesh.is_edge_manifold():
        print("Warning: Mesh has non-manifold edges. Repairing may not work as expected.")

    # Detect boundary edges (open boundaries)
    boundary_edges = mesh.get_non_manifold_edges(allow_boundary_edges=True)
    if len(boundary_edges) == 0:
        print("No open boundaries detected.")

    # Detect boundary loops
    boundary_loops = detect_boundary_loops(mesh)
    if not boundary_loops:
        print("No boundary loops found.")

        # Ensure that a PyVista mesh is created and returned
        vertices = np.asarray(mesh.vertices)
        triangles = np.asarray(mesh.triangles)

        if len(triangles) == 0:
            logging.error("No triangles were generated in the sphere mesh.")
            return None, None  # Prevent breaking the unpacking

        faces = np.hstack([[3] + list(tri) for tri in triangles])
        pv_mesh = pv.PolyData(vertices, faces)

        # Save the mesh vertices to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as temp_file:
            np.savetxt(temp_file.name, pv_mesh.points)
            temp_file_path = temp_file.name

        logging.info("Returning a valid tuple for sphere case.")
        return temp_file_path, pv_mesh  # Ensure the return is a tuple!

    # Fill small holes
    for loop in boundary_loops:
        if not loop:
            logging.warning("Empty boundary loop encountered. Skipping.")
            continue

        # Extract boundary points
        loop_points = np.asarray(mesh.vertices)[loop]

        # Calculate the perimeter of the boundary loop
        perimeter = np.sum(np.linalg.norm(np.diff(loop_points, axis=0, append=loop_points[:1]), axis=1))
        if perimeter > 5*radii[-1]:
            logging.info(f"Skipping boundary loop with perimeter {perimeter:.4f} (larger than max_hole_size).")
            continue

        # Fill only if it is a very small hole (typically triangular)
        if len(loop) == 3 or perimeter < 3 * radii[-1]:
            logging.info(f"Filling small triangular hole with perimeter {perimeter:.4f}.")
            
            # Create a new face from the three boundary points
            new_face = np.array([[loop[0], loop[1], loop[2]]])

            # Append new face to the mesh
            new_vertices = np.asarray(mesh.vertices)
            mesh.triangles = o3d.utility.Vector3iVector(
                np.vstack([np.asarray(mesh.triangles), new_face])
            )

        # Check if the boundary points are planar
        planar, normal = is_planar(loop_points)
        if planar:
            # Fill the hole directly with triangulation
            triangles = fill_planar_hole(loop_points)
            new_faces = np.array([[loop[i] for i in tri] for tri in triangles])
        else:
            # Use convex hull for non-planar holes
            boundary_cloud = o3d.geometry.PointCloud()
            boundary_cloud.points = o3d.utility.Vector3dVector(loop_points)

            # Compute the convex hull, handle invalid results
            try:
                boundary_hull, _ = boundary_cloud.compute_convex_hull()
                new_faces = np.asarray(boundary_hull.triangles) + len(mesh.vertices)
            except Exception as e:
                logging.error(f"Failed to compute convex hull for boundary loop: {e}")
                continue

        # Append new vertices and faces to the original mesh
        new_vertices = np.vstack([np.asarray(mesh.vertices), loop_points])
        mesh.vertices = o3d.utility.Vector3dVector(new_vertices)
        mesh.triangles = o3d.utility.Vector3iVector(
            np.vstack([np.asarray(mesh.triangles), new_faces])
        )

    # Clean the mesh after filling
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    mesh.remove_non_manifold_edges()

    # Repair the mesh
    if not mesh.is_edge_manifold():
        print("Warning: Mesh has non-manifold edges. Repairing may not work as expected.")

    # Detect boundary edges (open boundaries)
    boundary_edges = mesh.get_non_manifold_edges(allow_boundary_edges=True)
    if len(boundary_edges) == 0:
        print("No open boundaries detected.")

    # Detect boundary loops
    boundary_loops = detect_boundary_loops(mesh)
    if not boundary_loops:
        print("No boundary loops found.")

        # Ensure that a PyVista mesh is created and returned
        vertices = np.asarray(mesh.vertices)
        triangles = np.asarray(mesh.triangles)

        if len(triangles) == 0:
            logging.error("No triangles were generated in the sphere mesh.")
            return None, None  # Prevent breaking the unpacking

        faces = np.hstack([[3] + list(tri) for tri in triangles])
        pv_mesh = pv.PolyData(vertices, faces)

        # Save the mesh vertices to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as temp_file:
            np.savetxt(temp_file.name, pv_mesh.points)
            temp_file_path = temp_file.name

        logging.info("Returning a valid tuple for sphere case.")
        return temp_file_path, pv_mesh  # Ensure the return is a tuple!

    # Fill small holes
    for loop in boundary_loops:
        if not loop:
            logging.warning("Empty boundary loop encountered. Skipping.")
            continue

        # Extract boundary points
        loop_points = np.asarray(mesh.vertices)[loop]

        # Calculate the perimeter of the boundary loop
        perimeter = np.sum(np.linalg.norm(np.diff(loop_points, axis=0, append=loop_points[:1]), axis=1))
        if perimeter > 5*radii[-1]:
            logging.info(f"Skipping boundary loop with perimeter {perimeter:.4f} (larger than max_hole_size).")
            continue

        # Fill only if it is a very small hole (typically triangular)
        if len(loop) == 3 or perimeter < 3 * radii[-1]:
            logging.info(f"Filling small triangular hole with perimeter {perimeter:.4f}.")
            
            # Create a new face from the three boundary points
            new_face = np.array([[loop[0], loop[1], loop[2]]])

            # Append new face to the mesh
            new_vertices = np.asarray(mesh.vertices)
            mesh.triangles = o3d.utility.Vector3iVector(
                np.vstack([np.asarray(mesh.triangles), new_face])
            )

        # Check if the boundary points are planar
        planar, normal = is_planar(loop_points)
        if planar:
            # Fill the hole directly with triangulation
            triangles = fill_planar_hole(loop_points)
            new_faces = np.array([[loop[i] for i in tri] for tri in triangles])
        else:
            # Use convex hull for non-planar holes
            boundary_cloud = o3d.geometry.PointCloud()
            boundary_cloud.points = o3d.utility.Vector3dVector(loop_points)

            # Compute the convex hull, handle invalid results
            try:
                boundary_hull, _ = boundary_cloud.compute_convex_hull()
                new_faces = np.asarray(boundary_hull.triangles) + len(mesh.vertices)
            except Exception as e:
                logging.error(f"Failed to compute convex hull for boundary loop: {e}")
                continue

        # Append new vertices and faces to the original mesh
        new_vertices = np.vstack([np.asarray(mesh.vertices), loop_points])
        mesh.vertices = o3d.utility.Vector3dVector(new_vertices)
        mesh.triangles = o3d.utility.Vector3iVector(
            np.vstack([np.asarray(mesh.triangles), new_faces])
        )

    # Clean the mesh after filling
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    mesh.remove_non_manifold_edges()

    # Convert back to PyVista mesh
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)
    faces = np.hstack([[3] + list(tri) for tri in triangles])
    pv_mesh = pv.PolyData(vertices, faces)
    logging.info("Filling small holes in the mesh...")
    pv_mesh = pv_mesh.fill_holes(hole_size=(radii[-1]))

    # # Visualize the final mesh with original points overlay
    # logging.info("Visualizing the mesh with original points overlay...")
    # plotter = pv.Plotter()
    # plotter.add_mesh(pv_mesh, show_edges=False, color="lightblue", label="Mesh")
    # # plotter.add_points(points, color="red", point_size=5, label="Original Points")
    # plotter.add_legend()
    # plotter.show()

    # Save the mesh vertices to a temporary file
    logging.info("Saving PyVista mesh vertices to a temporary file...")
    with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as temp_file:
        np.savetxt(temp_file.name, pv_mesh.points)
        temp_file_path = temp_file.name

    logging.info("Exiting create_mesh_with_curvature()")
    return temp_file_path, pv_mesh

def is_planar(points, tolerance=1e-2):
    """
    Check if a set of points is approximately planar.

    Args:
        points (np.ndarray): Array of points (Nx3).
        tolerance (float): Threshold for planarity.

    Returns:
        bool, np.ndarray: True if planar, and the plane normal if planar.
    """
    if len(points) < 3:
        return False, None

    # Compute the normal of the plane using the first three points
    v1 = points[1] - points[0]
    v2 = points[2] - points[0]
    normal = np.cross(v1, v2)
    normal /= np.linalg.norm(normal)

    # Check the distance of all points from the plane
    distances = np.dot(points - points[0], normal)
    if np.all(np.abs(distances) < tolerance):
        return True, normal
    return False, None


def fill_planar_hole(loop_points):
    """
    Fill a planar hole by triangulating its boundary.

    Args:
        loop_points (np.ndarray): Points of the boundary loop (Nx3).

    Returns:
        np.ndarray: Triangles that fill the hole.
    """
    # Project points onto a plane (2D)
    centroid = np.mean(loop_points, axis=0)
    v1 = loop_points[1] - loop_points[0]
    v1 /= np.linalg.norm(v1)
    normal = np.cross(v1, loop_points[2] - loop_points[0])
    normal /= np.linalg.norm(normal)
    v2 = np.cross(normal, v1)

    # Create 2D coordinates for triangulation
    plane_points = np.dot(loop_points - centroid, np.vstack((v1, v2)).T)

    # Triangulate in 2D
    delaunay = Delaunay(plane_points)
    triangles = delaunay.simplices

    return triangles


def detect_boundary_loops(mesh):
    """
    Detect boundary loops in an Open3D TriangleMesh.

    Args:
        mesh (o3d.geometry.TriangleMesh): The input mesh.

    Returns:
        List[List[int]]: A list of boundary loops, each represented as a list of vertex indices.
    """
    edges = {}
    triangles = np.asarray(mesh.triangles)

    # Count occurrences of each edge
    for tri in triangles:
        for i in range(3):
            edge = tuple(sorted((tri[i], tri[(i + 1) % 3])))
            if edge in edges:
                edges[edge] += 1
            else:
                edges[edge] = 1

    # Extract boundary edges (shared by only one triangle)
    boundary_edges = [edge for edge, count in edges.items() if count == 1]

    # Group boundary edges into loops
    loops = []
    while boundary_edges:
        loop = []
        edge = boundary_edges.pop(0)
        loop.extend(edge)

        while True:
            # Find the next edge that connects to the current loop
            connected = [e for e in boundary_edges if loop[-1] in e]
            if not connected:
                break
            next_edge = connected[0]
            boundary_edges.remove(next_edge)
            loop.append(next_edge[1] if next_edge[0] == loop[-1] else next_edge[0])

        loops.append(loop)

    return loops


##################################
def average_distance_using_kd_tree(pcd):
    logging.info("Calculating average distance between points")

    # Convert Open3D PointCloud to a numpy array
    points = np.asarray(pcd.points)
    num_points = points.shape[0]

    if num_points < 2:
        raise ValueError("Point cloud must contain at least two points.")

    # Use systematic sampling for better coverage
    sample_size = min(1000, num_points)  # Use all points if fewer than 1000
    sampled_points = points[np.random.choice(num_points, sample_size, replace=False)]

    # Create a KDTree for efficient nearest neighbor search
    tree = KDTree(points)

    # Calculate average distance to the nearest neighbor
    distances = []
    for point in sampled_points:
        dist, _ = tree.query(point, k=2)  # k=2 includes the point itself
        distances.append(dist[1])  # Exclude the point itself

    average_distance = np.mean(distances)
    logging.info(f"Computed average distance: {average_distance}")

    # Define BPA radii dynamically based on the point cloud's scale
    radii_list = np.linspace(0.01 * average_distance, 25 * average_distance, 1000)

    return {'average_distance': average_distance, 'radii_list': radii_list}



##################################
def validate_shape(file_path, flag):
    logging.info("Inside validate_shape()")
    temp_file_path, mesh = create_mesh_with_curvature(file_path)

    if temp_file_path:
        pcl = PointCloud(temp_file_path)

        # Ensure KD-Tree is planted
        pcl.plant_kdtree(k_neighbors=100)

        print("Running neighbor study")
        converged_neighbors_int = pcl.explicit_quadratic_neighbor_study()
        print(f"Converged Num of neighbors from explicit_quadratic_neighbor_study is {converged_neighbors_int}")

        if converged_neighbors_int is None or converged_neighbors_int < 10:
            logging.error("Neighbor study failed: Not enough valid neighbors found.")
            return 0, 0, 0  # Prevent NaNs

        print("Calculating quadratic surfaces")
        pcl.fit_explicit_quadratic_surfaces_to_neighborhoods()

        print("Calculating quadratic curvatures")
        gaussian_curvature, mean_curvature = pcl.calculate_curvatures_of_explicit_quadratic_surfaces_for_all_points()

        # Check NaN values
        num_nan_gaussian = np.sum(np.isnan(gaussian_curvature))
        num_nan_mean = np.sum(np.isnan(mean_curvature))
        total_points = len(gaussian_curvature)

        logging.warning(f"NaN Gaussian Curvature: {num_nan_gaussian}/{total_points} ({100 * num_nan_gaussian / total_points:.2f}%)")
        logging.warning(f"NaN Mean Curvature: {num_nan_mean}/{total_points} ({100 * num_nan_mean / total_points:.2f}%)")

        if num_nan_gaussian > 0.8 * total_points:
            logging.error("Too many NaN curvatures. Exiting early.")
            return 0, 0, 0

        print("Saving to PLY format")
        points = pcl.points

        with open('output_with_curvatures.ply', 'w') as ply_file:
            ply_file.write('ply\n')
            ply_file.write('format ascii 1.0\n')
            ply_file.write(f'element vertex {len(points)}\n')
            ply_file.write('property float x\n')
            ply_file.write('property float y\n')
            ply_file.write('property float z\n')
            ply_file.write('property float gaussian_curvature\n')
            ply_file.write('property float mean_curvature\n')
            ply_file.write('end_header\n')

            for i in range(len(points)):
                ply_file.write(f'{points[i][0]} {points[i][1]} {points[i][2]} '
                               f'{gaussian_curvature[i]} {mean_curvature[i]}\n')

        print("Point cloud with curvatures saved successfully.")

        pv_mesh = mesh
        pv_mesh.point_data['gaussian_curvature'] = gaussian_curvature
        pv_mesh.point_data['mean_curvature'] = mean_curvature

        mean_curvature_squared = [item * item for item in mean_curvature]
        pv_mesh.point_data['mean_curvature_squared'] = mean_curvature_squared

        computed_bending_energy, computed_stretching_energy, computed_total_area = load_mesh_compute_energies(pv_mesh)

        # Prevent division by zero in Z-score calculations
        gaussian_std = np.std(gaussian_curvature)
        mean_std = np.std(mean_curvature_squared)
        if gaussian_std == 0:
            gaussian_std = 1e-6
        if mean_std == 0:
            mean_std = 1e-6

        # Ask the user if they want to filter outliers
        if flag is not "N":
            filter_outliers = input("Would you like to filter curvature outliers? (Y/N): ").strip().upper()

        if filter_outliers == "Y" and flag is not "N":
            # Define Z-score thresholds to test
            z_thresholds = [7, 5, 3, 2, 1]

            # Loop through different Z-score thresholds and generate plots
            for z_threshold in z_thresholds:
                print(f"Filtering with Z-score threshold: {z_threshold}")

                # Filter curvature outliers
                gaussian_z_scores = np.abs((gaussian_curvature - np.mean(gaussian_curvature)) / gaussian_std)
                gaussian_filtered = np.where(gaussian_z_scores > z_threshold, np.nan, gaussian_curvature)

                mean_curvature_squared_z_scores = np.abs((mean_curvature_squared - np.mean(mean_curvature_squared)) / mean_std)
                mean_curvature_squared_filtered = np.where(mean_curvature_squared_z_scores > z_threshold, np.nan, mean_curvature_squared)

                # Replace outliers in mesh for this threshold
                pv_mesh.point_data['gaussian_curvature'] = gaussian_filtered
                pv_mesh.point_data['mean_curvature_squared'] = mean_curvature_squared_filtered

                gaussian_min = np.nanmin(gaussian_filtered)  # Use nanmin to ignore NaNs
                gaussian_max = np.nanmax(gaussian_filtered)
                gaussian_clim = [gaussian_min, gaussian_max]

                mean_min = np.nanmin(mean_curvature_squared_filtered)
                mean_max = np.nanmax(mean_curvature_squared_filtered)
                mean_clim = [mean_min, mean_max]

                sargs = dict(
                    title=f"Filtered at {z_threshold} std deviations",
                    title_font_size=20,
                    label_font_size=16,
                    shadow=True,
                    n_labels=3,
                    italic=True,
                    fmt="%.6f",
                    font_family="arial",
                )

                # Plot Gaussian curvature for this threshold
                pv_mesh.plot(show_edges=False, scalars='gaussian_curvature', cmap='viridis', clim=gaussian_clim, scalar_bar_args=sargs)

                # Plot Mean curvature squared for this threshold
                pv_mesh.plot(show_edges=False, scalars='mean_curvature_squared', cmap='plasma', clim=mean_clim, scalar_bar_args=sargs)

            return computed_bending_energy, computed_stretching_energy, computed_total_area

        else:
            # If user chooses not to filter, use original values
            pv_mesh.point_data['gaussian_curvature'] = gaussian_curvature
            pv_mesh.point_data['mean_curvature_squared'] = mean_curvature_squared
            print("No outliers filtered.")
        
            gaussian_min = np.min(gaussian_curvature)
            gaussian_max = np.max(gaussian_curvature)

            gaussian_clim = [gaussian_min, gaussian_max]

            mean_min = np.min(mean_curvature_squared)
            mean_max = np.max(mean_curvature_squared)

            mean_clim = [mean_min, mean_max]

            sargs = dict(
            title_font_size=20,
            label_font_size=16,
            shadow=True,
            n_labels=3,
            italic=True,
            fmt="%.6f",
            font_family="arial",
            )

            # Plot Gaussian curvature with color scale limits set to 1 std deviation from mean
            pv_mesh.plot(show_edges=False, scalars='gaussian_curvature', cmap='viridis', clim=gaussian_clim, scalar_bar_args=sargs)

            # Plot Mean curvature squared with color scale limits set to 1 std deviation from mean
            pv_mesh.plot(show_edges=False, scalars='mean_curvature_squared', cmap='plasma', clim=mean_clim, scalar_bar_args=sargs)
            
            return computed_bending_energy, computed_stretching_energy, computed_total_area

    else:
        logging.error("Failed to create or load mesh.")
        return 0, 0, 0

    


def convert_pv_to_o3d(pv_mesh):
    """
    Convert a PyVista mesh to an Open3D TriangleMesh.
    """
    # Extract vertices and faces
    vertices = np.array(pv_mesh.points)
    faces = np.array(pv_mesh.faces).reshape(-1, 4)[:, 1:]  # Reshape and remove first column

    # Create Open3D mesh
    o3d_mesh = o3d.geometry.TriangleMesh()
    o3d_mesh.vertices = o3d.utility.Vector3dVector(vertices)
    o3d_mesh.triangles = o3d.utility.Vector3iVector(faces)
    
    # Optionally add vertex normals if they exist
    if 'normals' in pv_mesh.point_data:
        o3d_mesh.vertex_normals = o3d.utility.Vector3dVector(pv_mesh.point_data['normals'])
    
    return o3d_mesh

##################################
def load_mesh_compute_energies(mesh):
    o3d_mesh = convert_pv_to_o3d(mesh)
    logging.info("Inside load_mesh_compute_energies()")
    
    if o3d_mesh is None:
        logging.error("Error: Mesh conversion failed.")
        return 0, 0, 0  # Return all three values as zero
    
    if not o3d_mesh.has_triangles():
        logging.error("Mesh has no valid triangles.")
        return 0, 0, 0  # Return all three values as zero

    # Compute cell areas manually
    o3d_mesh.compute_triangle_normals()
    vertices = np.asarray(o3d_mesh.vertices)
    triangles = np.asarray(o3d_mesh.triangles)

    if len(triangles) == 0:
        logging.error("Error: No triangles detected in the mesh.")
        return 0, 0, 0  # Return all three values as zero

    areas = np.zeros(len(triangles))
    for i, tri in enumerate(triangles):
        v0 = vertices[tri[0]]
        v1 = vertices[tri[1]]
        v2 = vertices[tri[2]]
        area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
        areas[i] = area

    if np.sum(areas) == 0:
        logging.error("Error: Computed areas are all zero.")
        return 0, 0, 0

    face_gaussian = np.zeros(len(triangles))
    face_mean = np.zeros(len(triangles))
    face_mean_squared = np.zeros(len(triangles))

    # Ensure Curvature Data Exists
    if 'gaussian_curvature' in mesh.point_data and 'mean_curvature' in mesh.point_data:
        gaussian_curvature = np.asarray(mesh.point_data['gaussian_curvature'])
        mean_curvature = np.asarray(mesh.point_data['mean_curvature'])
        mean_squared = mean_curvature ** 2
    else:
        logging.warning("Curvature data missing. Setting curvatures to zero.")
        gaussian_curvature = np.zeros(len(vertices))
        mean_curvature = np.zeros(len(vertices))
        mean_squared = np.zeros(len(vertices))

    for i, tri in enumerate(triangles):
        verts = np.array(tri)
        face_center = np.mean(vertices[verts], axis=0)

        # Calculate distances from each vertex to the face center
        distances = np.linalg.norm(vertices[verts] - face_center, axis=1)
        if np.sum(distances) == 0:
            logging.warning(f"Degenerate face detected at index {i}, skipping.")
            continue  # Skip bad faces

        weights = distances / np.sum(distances)

        # Handle missing curvature data
        face_gaussian[i] = np.sum(weights * gaussian_curvature[verts]) if gaussian_curvature.size > 0 else 0
        face_mean[i] = np.sum(weights * mean_curvature[verts]) if mean_curvature.size > 0 else 0
        face_mean_squared[i] = np.sum(weights * mean_squared[verts]) if mean_squared.size > 0 else 0

    # Compute Energies
    bending_energy = np.nansum(face_mean_squared * areas)
    stretching_energy = np.nansum(face_gaussian * areas)
    total_area = np.sum(areas)

    logging.info(f"Computed Bending Energy: {bending_energy}, Stretching Energy: {stretching_energy}, Area: {total_area}")
    logging.info("Exiting load_mesh_compute_energies()")
    
    return bending_energy, stretching_energy, total_area

##################################
def generate_pv_shapes(shape_name, num_points=10000, perturbation_strength=0.0, radius=10.0):
    def perturb_points(points, strength):
        perturbation = np.random.normal(scale=strength, size=points.shape)
        return points + perturbation

    # Sphere
    def generate_sphere_points(num_points, radius):
        indices = np.arange(0, num_points, dtype=float) + 0.5
        phi = np.arccos(1 - 2 * indices / num_points)  # Properly spaced in polar angle
        theta = np.pi * (1 + np.sqrt(5)) * indices    # Golden angle method for azimuthal angle
        x = radius * np.cos(theta) * np.sin(phi)
        y = radius * np.sin(theta) * np.sin(phi)
        z = radius * np.cos(phi)
        return np.vstack((x, y, z)).T

    # Cylinder
    def generate_cylinder_points(num_points, radius):
        height = 2 * radius  # Assume height equals diameter
        surface_area = 2 * np.pi * radius * height
        point_density = num_points / surface_area

        num_height_points = int(np.sqrt(num_points * height / (2 * np.pi * radius + height)))
        num_circumference_points = num_points // num_height_points

        z = np.linspace(-height / 2, height / 2, num_height_points)
        theta = np.linspace(0, 2 * np.pi, num_circumference_points, endpoint=False)
        theta, z = np.meshgrid(theta, z)

        x = radius * np.cos(theta)
        y = radius * np.sin(theta)

        return np.vstack([x.ravel(), y.ravel(), z.ravel()]).T

    # Torus
    def generate_torus_points(num_points, tube_radius, cross_section_radius):
        surface_area = (2 * np.pi * tube_radius) * (2 * np.pi * cross_section_radius)
        density = num_points / surface_area
        spacing = 1 / np.sqrt(density)

        points = []
        theta_spacing = spacing / tube_radius
        phi_spacing = spacing / cross_section_radius

        theta = 0
        while theta < 2 * np.pi:
            phi = 0
            while phi < 2 * np.pi:
                x = (tube_radius + cross_section_radius * np.cos(phi)) * np.cos(theta)
                y = (tube_radius + cross_section_radius * np.cos(phi)) * np.sin(theta)
                z = cross_section_radius * np.sin(phi)
                points.append([x, y, z])
                phi += phi_spacing
            theta += theta_spacing

        return np.array(points)

    # Egg-carton
    def generate_egg_carton_points(num_points):
        x = np.linspace(-3, 3, int(np.sqrt(num_points)) * 2)
        y = np.linspace(-3, 3, int(np.sqrt(num_points)) * 2)
        x, y = np.meshgrid(x, y)
        z = np.sin(x) * np.cos(y)
        return np.vstack([x.ravel(), y.ravel(), z.ravel()]).T

    # Generate the requested shape
    if shape_name == "sphere":
        points = generate_sphere_points(num_points, radius)
    elif shape_name == "cylinder":
        points = generate_cylinder_points(num_points, radius)
    elif shape_name == "torus":
        points = generate_torus_points(num_points, tube_radius=radius, cross_section_radius=radius / 3)
    elif shape_name == "egg_carton":
        points = generate_egg_carton_points(num_points)
    else:
        raise ValueError(f"Unknown shape: {shape_name}")

    # Create Open3D PointCloud object for BPA
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)

    # Ensure normals are estimated properly
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=radius * 0.1, max_nn=50))
    pcd.orient_normals_consistent_tangent_plane(k=50)

    # Create perturbed version
    perturbed_points = perturb_points(points, perturbation_strength)
    pcd_perturbed = o3d.geometry.PointCloud()
    pcd_perturbed.points = o3d.utility.Vector3dVector(perturbed_points)
    pcd_perturbed.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=radius * 0.1, max_nn=50))
    pcd_perturbed.orient_normals_consistent_tangent_plane(k=50)

    # Return Open3D-compatible point clouds (for BPA processing)
    return pcd, pcd_perturbed

def save_points_to_ply(points, filename):  
    with open(filename, 'w') as f:

        f.write('ply\n')
        f.write('format ascii 1.0\n')
        f.write(f'element vertex {len(points)}\n')
        f.write('property float x\n')
        f.write('property float y\n')
        f.write('property float z\n')
        f.write('end_header\n')

        np.savetxt(f, points, fmt='%.6f %.6f %.6f')

        print(f"point cloud saved in ply format as {filename}")


def parse_ply(file_path):
    try:
        with open(file_path, 'r') as file:
            # Read header
            while True:
                line = file.readline().strip()
                if line == "end_header":
                    print(f"Removed header from PLY")
                    break
            # Read body data
            points = []
            while True:
                line = file.readline()
                if not line:
                    break
                parts = line.split()
                x, y, z = map(float, parts[:3])
                points.append([x, y, z])
        logging.info("Assigned points from .ply to np array")
        return np.array(points)
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return None
    except Exception as e:
        print(f"Error parsing PLY file: {e}")

