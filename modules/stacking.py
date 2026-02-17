# filepath: tides_pipe/modules/stacking.py
import logging
import os
import numpy as np
from astropy.io import fits
from .module import Module
from typing import List, Tuple, Optional


class Stacking(Module):
    """
    Module for stacking multiple spectra with the same OBJ_UID from a single night.
    Performs weighted median stacking with NaN handling.
    """
    
    def __init__(self, config):
        super().__init__(config)
        self.logger = logging.getLogger(__name__)
    
    def weighted_median_stack(
        self, 
        wavelengths: List[np.ndarray], 
        fluxes: List[np.ndarray], 
        ivars: List[np.ndarray]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Perform weighted median stacking of multiple spectra.
        
        Parameters
        ----------
        wavelengths : List[np.ndarray]
            List of wavelength arrays (assumed to be identical or on same grid)
        fluxes : List[np.ndarray]
            List of flux arrays
        ivars : List[np.ndarray]
            List of inverse variance (FLUX_IVAR) arrays
        
        Returns
        -------
        wave : np.ndarray
            Stacked wavelength array
        flux_stacked : np.ndarray
            Weighted median stacked flux
        ivar_stacked : np.ndarray
            Combined inverse variance
        """
        if not fluxes or len(fluxes) == 0:
            raise ValueError("No spectra provided for stacking")
        
        if len(fluxes) != len(ivars):
            raise ValueError("Number of flux and ivar arrays must match")
        
        # Use the first wavelength array as reference
        wave = wavelengths[0].copy()
        n_pix = len(wave)
        n_spec = len(fluxes)
        
        # Stack flux and ivar arrays
        flux_stack = np.zeros((n_spec, n_pix))
        ivar_stack = np.zeros((n_spec, n_pix))
        
        for i, (flux, ivar) in enumerate(zip(fluxes, ivars)):
            # Guard against length mismatch
            if len(flux) != n_pix:
                self.logger.warning(f"Spectrum {i} has different length ({len(flux)} vs {n_pix}); skipping")
                continue
            flux_stack[i, :] = flux
            ivar_stack[i, :] = ivar
        
        # Use ivar directly as weights
        # Guard against zero/NaN ivars
        with np.errstate(divide='ignore', invalid='ignore'):
            weights = ivar_stack.copy()
            weights[~np.isfinite(weights)] = 0.0  # Set inf/nan weights to zero
            weights[ivar_stack <= 0] = 0.0  # Zero out invalid ivars
        
        # Weighted median stacking per pixel
        flux_stacked = np.zeros(n_pix)
        ivar_stacked = np.zeros(n_pix)
        
        for j in range(n_pix):
            flux_col = flux_stack[:, j]
            weight_col = weights[:, j]
            
            # Mask out NaN fluxes and zero weights
            valid_mask = np.isfinite(flux_col) & (weight_col > 0)
            
            if np.sum(valid_mask) == 0:
                # No valid data for this pixel
                flux_stacked[j] = np.nan
                ivar_stacked[j] = 0.0
                continue
            
            valid_flux = flux_col[valid_mask]
            valid_weights = weight_col[valid_mask]
            
            if len(valid_flux) == 1:
                # Only one valid spectrum for this pixel
                flux_stacked[j] = valid_flux[0]
                ivar_stacked[j] = ivar_stack[valid_mask, j][0]
            else:
                # Compute weighted median
                flux_stacked[j] = self._weighted_median(valid_flux, valid_weights)
                
                # Ivar propagation: sum of ivars for independent measurements
                valid_ivars = ivar_stack[valid_mask, j]
                ivar_stacked[j] = np.sum(valid_ivars)
        
        return wave, flux_stacked, ivar_stacked
    
    def _weighted_median(self, values: np.ndarray, weights: np.ndarray) -> float:
        """
        Compute weighted median.
        
        Parameters
        ----------
        values : np.ndarray
            Data values
        weights : np.ndarray
            Weights for each value
        
        Returns
        -------
        float
            Weighted median
        """
        # Sort by values
        sorted_indices = np.argsort(values)
        sorted_values = values[sorted_indices]
        sorted_weights = weights[sorted_indices]
        
        # Normalize weights
        weight_sum = np.sum(sorted_weights)
        if weight_sum <= 0:
            return np.median(values)  # Fall back to unweighted median
        
        cumulative_weight = np.cumsum(sorted_weights) / weight_sum
        
        # Find median (where cumulative weight crosses 0.5)
        median_idx = np.searchsorted(cumulative_weight, 0.5)
        
        # Handle edge cases
        if median_idx >= len(sorted_values):
            median_idx = len(sorted_values) - 1
        
        return sorted_values[median_idx]
    
    def stack_spectra_by_obj_uid(
        self,
        spectra_data: List[dict],
        output_dir: str
    ) -> List[dict]:
        """
        Stack spectra that share the same OBJ_UID.
        
        Parameters
        ----------
        spectra_data : List[dict]
            List of spectrum metadata dictionaries with keys:
            - 'obj_uid': OBJ_UID value
            - 'wavelength': np.ndarray
            - 'flux': np.ndarray
            - 'error': np.ndarray
            - 'filepath': str (original spectrum file path)
            - 'metadata': dict (additional metadata)
        output_dir : str
            Directory to save stacked spectra
        
        Returns
        -------
        List[dict]
            List of stacked spectrum metadata dictionaries
        """
        # Group by OBJ_UID
        uid_groups = {}
        for spec in spectra_data:
            uid = spec.get('obj_uid')
            if uid is None:
                continue
            if uid not in uid_groups:
                uid_groups[uid] = []
            uid_groups[uid].append(spec)
        
        stacked_spectra = []
        
        for obj_uid, group in uid_groups.items():
            if len(group) < 2:
                # No stacking needed for single spectrum
                self.logger.debug(f"OBJ_UID {obj_uid}: only one spectrum, skipping stack")
                continue
            
            self.logger.info(f"Stacking {len(group)} spectra for OBJ_UID {obj_uid}")
            
            try:
                # Extract data arrays
                wavelengths = [s['wavelength'] for s in group]
                fluxes = [s['flux'] for s in group]
                ivars = [s['ivar'] for s in group]
                
                # Perform stacking
                wave_stacked, flux_stacked, ivar_stacked = self.weighted_median_stack(
                    wavelengths, fluxes, ivars
                )
                
                # Create stacked spectrum metadata
                stacked_meta = {
                    'obj_uid': obj_uid,
                    'wavelength': wave_stacked,
                    'flux': flux_stacked,
                    'ivar': ivar_stacked,
                    'n_stacked': len(group),
                    'source_files': [s['filepath'] for s in group],
                    'source_specuids': [s.get('tides_specid') for s in group],
                    'metadata': group[0].get('metadata', {}).copy()  # Inherit metadata from first
                }
                
                # Add stacking note to metadata
                if 'metadata' in stacked_meta:
                    stacked_meta['metadata']['STACKED'] = True
                    stacked_meta['metadata']['N_STACKED'] = len(group)
                
                stacked_spectra.append(stacked_meta)
                
                self.logger.info(f"Successfully stacked {len(group)} spectra for OBJ_UID {obj_uid}")
                
            except Exception as e:
                self.logger.error(f"Failed to stack spectra for OBJ_UID {obj_uid}: {e}")
                continue
        
        return stacked_spectra
    
    def save_stacked_spectrum(
        self,
        stacked_data: dict,
        output_path: str,
        format: str = 'txt'
    ):
        """
        Save stacked spectrum to file.
        
        Parameters
        ----------
        stacked_data : dict
            Dictionary with 'wavelength', 'flux', 'error' arrays
        output_path : str
            Output file path
        format : str
            Output format ('txt' or 'fits')
        """
        wave = stacked_data['wavelength']
        flux = stacked_data['flux']
        ivar = stacked_data['ivar']
        
        if format == 'txt':
            # Convert ivar to error for text output
            with np.errstate(divide='ignore', invalid='ignore'):
                error = 1.0 / np.sqrt(ivar)
                error[~np.isfinite(error)] = np.nan
            
            with open(output_path, 'w') as f:
                f.write("# Stacked Spectrum\n")
                f.write(f"# Number of spectra stacked: {stacked_data.get('n_stacked', 'unknown')}\n")
                f.write(f"# Source files: {', '.join(stacked_data.get('source_files', []))}\n")
                f.write("# Wavelength Flux Error Quality\n")
                for w, fl, err in zip(wave, flux, error):
                    # Quality flag: 0 for good, 1 for bad (based on ivar)
                    qual = 0 if np.isfinite(err) and err > 0 else 1
                    f.write(f"{w} {fl} {err} {qual}\n")
        elif format == 'fits':
            # Save as FITS with WAVE, FLUX, IVAR extensions
            col_wave = fits.Column(name='WAVE', array=wave, format='D')
            col_flux = fits.Column(name='FLUX', array=flux, format='D')
            col_ivar = fits.Column(name='IVAR', array=ivar, format='D')
            
            table_hdu = fits.BinTableHDU.from_columns([col_wave, col_flux, col_ivar])
            table_hdu.header['N_STACK'] = (stacked_data.get('n_stacked', 0), 'Number of spectra stacked')
            table_hdu.header['OBJ_UID'] = (stacked_data.get('obj_uid', ''), 'OBJ_UID')
            
            primary_hdu = fits.PrimaryHDU()
            hdul = fits.HDUList([primary_hdu, table_hdu])
            hdul.writeto(output_path, overwrite=True)
        else:
            raise ValueError(f"Unsupported format: {format}")
        
        self.logger.info(f"Saved stacked spectrum to {output_path}")
