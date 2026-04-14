export const phantomOverlay = {
  name: 'phantom_wave',
  totalStep: 1,
  createPointFigures: ({ overlay, coordinate, bounding, yAxis, xAxis }) => {
    // The extendData holds the 3 scenarios
    const scenarios = overlay.extendData?.scenarios || [];
    if (!scenarios.length) return [];

    const figures = [];

    scenarios.forEach(scenario => {
      const geometry = scenario.phantom_geometry || [];
      if (!geometry.length) return;

      const isDominant = scenario.probability > 33.4; // rough check for dominant
      const opacity = isDominant ? 0.6 : 0.15;
      
      // Determine color based on scenario
      let color = 'rgba(255, 215, 0, ' + opacity + ')'; // Default Action Gold
      
      if (scenario.type === 'structural_breakdown') {
        color = 'rgba(239, 83, 80, ' + opacity + ')'; // Warning Red
      } else if (scenario.type === 'zhongshu_oscillation') {
        color = 'rgba(169, 169, 169, ' + opacity + ')'; // Gray
      } else if (scenario.type === 'right_side_major_wave') {
        color = 'rgba(255, 215, 0, ' + (isDominant ? 0.8 : 0.4) + ')'; // Gold glow
      }

      // Convert geometry to canvas coordinates
      const coordinates = geometry.map(bar => {
        const x = xAxis.convertToPixel(bar.timestamp);
        // pure block structure, use min/max of open/close
        const yTop = yAxis.convertToPixel(Math.max(bar.open, bar.close));
        const yBottom = yAxis.convertToPixel(Math.min(bar.open, bar.close));
        return { x, yTop, yBottom };
      });

      // Draw pure block path by creating a polygon
      if (coordinates.length > 1) {
        const polyPoints = [];
        // Forward along top
        for (let i = 0; i < coordinates.length; i++) {
          polyPoints.push({ x: coordinates[i].x, y: coordinates[i].yTop });
        }
        // Backward along bottom
        for (let i = coordinates.length - 1; i >= 0; i--) {
          polyPoints.push({ x: coordinates[i].x, y: coordinates[i].yBottom });
        }

        figures.push({
          type: 'polygon',
          attrs: { coordinates: polyPoints },
          styles: { color: color, style: 'fill' }
        });
      }

      // 绘制 Exit Guardian ATR Line (如果包含的话，在这里画)
      if (scenario.type === 'structural_breakdown' && isDominant) {
        // Find the lowest point of breakdown
        const minLows = geometry.map(b => b.low);
        const minTarget = Math.min(...minLows);
        const targetY = yAxis.convertToPixel(minTarget);
        
        figures.push({
          type: 'line',
          attrs: { 
            coordinates: [
              { x: coordinates[0].x, y: targetY },
              { x: coordinates[coordinates.length - 1].x, y: targetY }
            ]
          },
          styles: {
            color: '#ef5350', // --warning-red
            size: 2,
            style: 'dashed'
          }
        });
        
        figures.push({
          type: 'text',
          attrs: {
            x: coordinates[coordinates.length - 1].x,
            y: targetY - 10,
            text: `PHYSICAL STOP LOSS: ¥${minTarget.toFixed(2)}`
          },
          styles: {
            color: '#ef5350',
            size: 12,
            family: 'JetBrains Mono'
          }
        });
      }
    });

    return figures;
  }
};
