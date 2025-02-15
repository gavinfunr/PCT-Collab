# point-cloud-toolbox


Python library for point cloud processing. The main emphasis is on discrete curvature measures, for now. 

The main approach (used in run.py) for calculating the approximate discrete curvatures is essentially:
1. A k-dimensional tree is constructed from the input point cloud to organize the data spatially.
2. For each point, the k nearest neighbors are determined using the k-d tree, supporting both distance-based and epsilon-ball queries.
3. The singular value decomposition (SVD) is employed on each neighborhood to ascertain the characteristic plane, aligning with the first two eigenvectors.
4. The neighborhood is rotated to align this plane with the xy-axis, repositioning the central point at the origin. The z-axis is approximated as the normal.
5. Consistency in curvature signs is ensured by adjusting the orientation based on the dot product between the z-axis and vectors in the neighborhood.
6. A quadratic surface is fitted to the neighborhood points using least-squares regression on a cost-function learning basis, yielding an explicit function F(x, y) = z, represented by using weights as coefficients.
7. Curvatures are computed using classical differential geometry sources such as Do Carmo, Spivak, and Gauss

The utilities live within the PointCloud class in pointCloudToolbox.py, you can see the implementation of known expressions for curvature within.

The work presented here is part of active research at the University of Nevada, Reno - please contact me if you would like to talk about the tools within.
rhutton@unr.edu

Below are some point clouds I have scanned/generated/downloaded which represent hypersurfaces in Euclidian 3-space. The points have been colored according to the calculated curvatures at the point.

![alt text](https://github.com/masnottuh/point-cloud-toolbox/blob/main/img/bunny1.png)
![alt text](https://github.com/masnottuh/point-cloud-toolbox/blob/main/img/bunny2.png)
![alt text](https://github.com/masnottuh/point-cloud-toolbox/blob/main/img/carton1.png)
![alt text](https://github.com/masnottuh/point-cloud-toolbox/blob/main/img/carton2.png)
![alt text](https://github.com/masnottuh/point-cloud-toolbox/blob/main/img/sridge.png)
![alt text](https://github.com/masnottuh/point-cloud-toolbox/blob/main/img/torus.png)



utils line 502 "is not" to "!="

what changed
More point densities iterations for shapes
split functionalities into different scripts
 - main_shape_validation.py runs through energy calcs for test shapes for lots of different point densities and some perturbs and then plots the theor area results v real err

- main_scans.py specific a directory to run the energy calcs in

- downsample.py uses o3d "voxel_down_sample" to reduce the size of the point clouds

- convert_asc_to_ply.py as it says on the tin. Just changing the headers of the underlying text files

- 
