####################################################################
# Robert Hutton - UNR Dept of Mech E - rhutton@unr.edu
####################################################################
####################################################################
# FOR ANALYZING PROGRAM AGAINST KNOWN GEOMETRIES
####################################################################
from utils import *
from pointCloudToolbox import *
import os
import logging
import pandas as pd
import subprocess
import sys
from scipy.integrate import dblquad
import glob
import wakepy

# keep awake for long tests
wakepy.set_keepawake(True)

##################################
logging.basicConfig(level=logging.INFO)
##################################

output_dir = './output'  # Output directory
test_shapes_dir = './test_shapes'
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

if not os.path.exists(test_shapes_dir):
    os.makedirs(test_shapes_dir)

# Parameters for testing
radii = [1.0, 10.0, 100.0, 1000]  # Radii for shapes
point_densities = [
    500, 1000, 2000, 3000, 4000, 5000, 7500, 10000, 12500, 15000, 
    17500, 20000, 22500, 25000, 30000, 35000, 40000, 45000, 50000, 
    60000, 70000, 80000, 90000, 100000, 125000
]

# Storage for results
results = []

# Function definitions for surface area integration
def egg_carton_surface_element(x, y):
    """Calculate the surface element for the egg carton function."""
    dzdx = np.cos(x) * np.cos(y)  # Partial derivative with respect to x
    dzdy = -np.sin(x) * np.sin(y)  # Partial derivative with respect to y
    return np.sqrt(1 + dzdx**2 + dzdy**2)


# Check for existing .ply files in the directory
existing_ply_files = glob.glob(f"{test_shapes_dir}/*.ply")
shape_names = ["sphere", "cylinder", "torus", "egg_carton"]
# Loop through radii and point densities
for shape_name in shape_names:
    for radius in radii:
        for num_points in point_densities:

            logging.info(f"Testing radius: {radius}, num_points: {num_points}")

            # Define the shape to be tested (change this as needed)
            shape_name = "sphere"  # Change this to "cylinder", "torus", or "egg_carton" as needed

            # Compute perturbation strength before generating the shape
            if shape_name == "sphere":
                perturbation_strength = 0.0001 * np.sqrt(radius)
            elif shape_name == "cylinder":
                perturbation_strength = 0.0001 * np.sqrt(radius * (2 * radius))  # Incorporating height
            elif shape_name == "torus":
                tube_radius = radius / 3
                perturbation_strength = 0.0001 * np.sqrt(radius * tube_radius)  # Scaling with both radii
            elif shape_name == "egg_carton":
                perturbation_strength = 0.0001 * radius  # Arbitrary, since curvature varies locally
            else:
                raise ValueError(f"Unknown shape: {shape_name}")

            # Generate only the requested shape
            shapes = generate_pv_shapes(shape_name, num_points=num_points, perturbation_strength=perturbation_strength, radius=radius)
            shape_names = [shape_name, f"{shape_name}_perturbed"]

            # Process generated shapes
            for shape, shape_name in zip(shapes, shape_names):
                points = shape.points
                filename = f"{test_shapes_dir}/{shape_name}_radius_{radius}_points_{num_points}.ply"
                save_points_to_ply(points, filename)

                # Process the saved shape
                loaded_shape = pv.read(filename)
                try:
                    bending_energy, stretching_energy, computed_area = validate_shape(filename, "N")
                    logging.info(f"Processed {shape_name}: Bending Energy: {bending_energy}, Stretching Energy: {stretching_energy}, Computed Area: {computed_area}")
                except Exception as e:
                    logging.error(f"Error processing {shape_name}: {e}")
                    bending_energy, stretching_energy, computed_area = "Error", "Error", "Error"

                # Calculate theoretical area
                theoretical_area = None
                if shape_name.startswith("sphere"):
                    theoretical_area = 4.0 * 3.14159 * (radius ** 2.0)
                elif shape_name.startswith("cylinder"):
                    height = 2 * radius
                    theoretical_area = 2.0 * ((3.14159 * radius) * height)
                elif shape_name.startswith("torus"):
                    tube_radius = radius
                    cross_section_radius = radius / 3
                    theoretical_area = (2 * 3.14159 * tube_radius) * (2 * 3.14159 * cross_section_radius)
                elif shape_name.startswith("egg_carton"):
                    theoretical_area, _ = dblquad(egg_carton_surface_element, -3, 3, lambda x: -3, lambda x: 3)

                # Append results
                results.append({
                    "Shape": shape_name,
                    "Radius": radius,
                    "Num Points": num_points,
                    "Theoretical Area": theoretical_area,
                    "Computed Area": computed_area,
                    "Bending Energy": bending_energy,
                    "Stretching Energy": stretching_energy
                })

        # Append results (no theoretical area for pre-existing shapes)
        results.append({
            "Shape": shape_name,
            "Radius": "N/A",
            "Num Points": "N/A",
            "Theoretical Area": "N/A",
            "Computed Area": computed_area,
            "Bending Energy": bending_energy,
            "Stretching Energy": stretching_energy
        })

    print("Completed testing for radius:", radius, "and num_points:", num_points)

# Save results to a DataFrame and display
results_df = pd.DataFrame(results)

# Save results to CSV
results_df.to_csv(f"shape_comparison_results.csv", index=False)

# Ensure 'Computed Area' and 'Theoretical Area' are numeric, replacing non-numeric values with NaN
results_df['Computed Area'] = pd.to_numeric(results_df['Computed Area'], errors='coerce')
results_df['Theoretical Area'] = pd.to_numeric(results_df['Theoretical Area'], errors='coerce')

# Calculate new column values
results_df['Points per Theoretical Area'] = results_df['Num Points'] / results_df['Theoretical Area']
results_df['Percent Error'] = 100 * (results_df['Computed Area'] - results_df['Theoretical Area']) / results_df['Theoretical Area']

# Create the new plot
for shape in results_df['Shape'].unique():
    df_shape = results_df[results_df['Shape'] == shape].copy()

    # Drop rows where required values are NaN
    df_shape = df_shape.dropna(subset=['Points per Theoretical Area', 'Percent Error'])

    if not df_shape.empty:  # Ensure there's data to plot
        # Create the plot
        plt.figure(figsize=(8, 6))
        plt.title(f"{shape} - Percent Error vs. Points/Theoretical Area", fontsize=20)
        plt.scatter(df_shape['Points per Theoretical Area'], df_shape['Percent Error'], marker='o', label=shape)
        plt.axhline(0, color='gray', linestyle='--', linewidth=1)  # Reference line at zero error
        plt.xlabel("Points / Theoretical Area", fontsize=18)
        plt.ylabel("Percent Error (%)", fontsize=18)
        plt.legend(fontsize=16)
        plt.grid(True)

        # Save and show the plot
        plt.savefig(f"{output_dir}/{shape}_error_vs_density.png")
        plt.show()


wakepy.set_keepawake(False)

